"""Certidão Negativa de Débitos Trabalhistas — CNDT / TST.

Site: https://cndt-certidao.tst.jus.br/gerarCertidao.faces
Validade: 180 dias (mas sempre lida de dentro do PDF quando possível).

O QUE FOI DESCOBERTO SOBRE O SITE (agosto/2026)
-----------------------------------------------
O levantamento inicial dizia "só CNPJ + verificação de segurança". Na prática,
essa "verificação de segurança" É UM CAPTCHA de 6 caracteres. Sem resolvê-lo,
nenhuma certidão sai.

Mecânica real da emissão, nesta ordem:
  1. GET  /gerarCertidao.faces  -> cookie de sessão + campo javax.faces.ViewState
  2. GET  /api                  -> JSON com {tokenDesafio, imagem[], audio[]}
                                   (os bytes vêm com sinal, como byte[] do Java:
                                    é preciso converter com & 0xFF)
  3. POST /gerarCertidao.faces  -> formulário + resposta do captcha (AJAX RichFaces)
  4. GET  /emissaoCertidao      -> o PDF em si

POR QUE httpx E NÃO PLAYWRIGHT
------------------------------
Este robô não abre navegador. A página é um JSF antigo e todo o fluxo cabe em
quatro requisições HTTP. Isso deixa o robô muito mais rápido, mais estável e
MUITO mais leve para o servidor do TST — um navegador dispara mais de dez
requisições de imagens, CSS e scripts a cada emissão, o que atrai bloqueio.

O captcha é lido pelo módulo robos/ocr_cndt.py, que funciona offline. Se um
serviço pago de captcha estiver configurado no config.yaml, ele é usado como
segunda opção quando a leitura local não tem confiança suficiente.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import httpx

from robos.base import (
    STATUS_NEGATIVA,
    STATUS_POSITIVA,
    STATUS_POSITIVA_COM_EFEITO_NEGATIVA,
    ResultadoConsulta,
    captcha_falhou,
    erro,
    formatar_cnpj,
    limpar_cnpj,
    normalizar,
    parece_pdf,
    salvar_debug,
    salvar_pdf,
    texto_do_pdf,
    validade_com_fallback,
)
from robos.captcha import resolver_por_servico
from robos.ocr_cndt import Biblioteca, ler

logger = logging.getLogger(__name__)

TIPO = "CNDT"
BASE = "https://cndt-certidao.tst.jus.br"
URL_EMISSAO = f"{BASE}/gerarCertidao.faces"
URL_CAPTCHA = f"{BASE}/api"
URL_DOWNLOAD = f"{BASE}/emissaoCertidao"

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

#: Padrão de quantos captchas diferentes tentar antes de desistir. Cada
#: tentativa pega uma imagem nova — errar um captcha não significa que o
#: próximo também vai falhar. Pode ser mudado no config.yaml.
TENTATIVAS_CAPTCHA = 4


# =============================================================================
#  AUXILIARES
# =============================================================================


def _bytes_do_java(lista) -> bytes:
    """O Java serializa byte[] com sinal (-128..127); o navegador usa Uint8Array."""
    return bytes((int(v) & 0xFF) for v in lista)


def _viewstate(html: str) -> str | None:
    """Extrai o javax.faces.ViewState, que o JSF exige de volta no POST."""
    for padrao in (
        r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"',
        r'value="([^"]*)"[^>]*name="javax\.faces\.ViewState"',
        r'id="javax\.faces\.ViewState[^"]*"[^>]*value="([^"]*)"',
    ):
        achado = re.search(padrao, html)
        if achado:
            return achado.group(1)
    return None


def _texto_visivel(html: str) -> str:
    limpo = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    limpo = re.sub(r"<[^>]+>", " ", limpo)
    return re.sub(r"\s+", " ", limpo).strip()


def _classificar(texto_pdf: str) -> str:
    """Decide o status a partir do texto do PDF emitido.

    O TST emite três documentos diferentes com o mesmo layout, e a diferença
    está só no texto. Positiva NÃO é erro: é uma certidão válida que informa
    pendência, e por isso também é guardada.
    """
    t = normalizar(texto_pdf)

    if "positiva com efeito" in t or "efeitos de negativa" in t or "efeito de negativa" in t:
        return STATUS_POSITIVA_COM_EFEITO_NEGATIVA
    # A negativa afirma que o CNPJ NÃO CONSTA do banco de devedores.
    if "nao consta" in t and "devedores trabalhistas" in t:
        return STATUS_NEGATIVA
    if "certidao positiva" in t or "consta do banco nacional" in t:
        return STATUS_POSITIVA
    if "certidao negativa" in t:
        return STATUS_NEGATIVA
    # Documento veio, mas não reconhecemos o texto: trata como negativa e o
    # número/validade lidos do PDF permitem conferência humana no histórico.
    logger.warning("Não reconheci o tipo da certidão pelo texto; assumindo negativa.")
    return STATUS_NEGATIVA


def _numero_da_certidao(texto_pdf: str) -> str | None:
    for padrao in (
        r"[Cc]ertid[ãa]o\s+n[º°.:]*\s*([\w./-]{6,40})",
        r"[Nn][úu]mero\s*[:.]?\s*([\w./-]{6,40})",
    ):
        achado = re.search(padrao, texto_pdf or "")
        if achado:
            return achado.group(1).strip(" .")
    return None


# =============================================================================
#  CAPTCHA
# =============================================================================


def _pegar_captcha(cliente: httpx.Client) -> tuple[str, bytes]:
    """Devolve (tokenDesafio, imagem PNG) da API do site."""
    resposta = cliente.get(URL_CAPTCHA, headers={"Referer": URL_EMISSAO})
    resposta.raise_for_status()
    dados = resposta.json()
    return dados["tokenDesafio"], _bytes_do_java(dados["imagem"])


def _resolver_captcha(imagem: bytes, contexto: dict, biblioteca: Biblioteca):
    """Tenta ler o captcha. Devolve (texto, confiavel, de_onde_veio)."""
    leitura = ler(imagem, biblioteca)
    if leitura.confiavel:
        return leitura.texto, True, "local", leitura

    logger.info("Leitura local do captcha ficou duvidosa (%s).", leitura)

    # Segunda opção: serviço pago, só se o usuário tiver configurado um.
    cfg = contexto.get("config")
    if cfg is not None and cfg.captcha_disponivel:
        texto = resolver_por_servico(imagem, cfg)
        if texto:
            return texto.strip().lower(), True, "servico", leitura

    # Sem serviço: manda mesmo assim o palpite local. Se estiver errado,
    # o site apenas recusa e tentamos outro captcha.
    return leitura.texto, False, "local-duvidoso", leitura


# =============================================================================
#  ROBÔ
# =============================================================================


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a CNDT do CNPJ informado."""
    numero = limpar_cnpj(cnpj)
    if len(numero) != 14:
        return erro(f"CNPJ inválido para a CNDT: {cnpj!r}", permanente=True)

    pasta_debug = contexto.get("pasta_debug")
    regras = contexto.get("regras") or {}
    tentativas_captcha = max(1, int(regras.get("tentativas_captcha", TENTATIVAS_CAPTCHA)))

    arquivo_aprendizado = contexto.get("arquivo_modelos_captcha")
    biblioteca = Biblioteca(arquivo_aprendizado)
    logger.info(
        "Biblioteca de captcha: %d modelos em %d classes.",
        biblioteca.total,
        biblioteca.classes,
    )

    ultimo_motivo = "não foi possível emitir"
    ultimo_html = None
    guardar_falhas = regras.get("guardar_captchas_que_falharam", True)
    captchas_nao_lidos: list[bytes] = []

    try:
        with httpx.Client(
            headers=CABECALHOS, timeout=90, follow_redirects=True
        ) as cliente:
            for tentativa in range(1, tentativas_captcha + 1):
                # Cada tentativa recomeça a sessão: o ViewState e o token do
                # captcha só valem para um envio.
                pagina = cliente.get(URL_EMISSAO)
                pagina.raise_for_status()
                estado = _viewstate(pagina.text)
                if not estado:
                    ultimo_html = pagina.text
                    ultimo_motivo = (
                        "não encontrei o campo javax.faces.ViewState na página — "
                        "o site do TST provavelmente mudou de layout"
                    )
                    break

                token, imagem = _pegar_captcha(cliente)
                texto, confiavel, origem, leitura = _resolver_captcha(
                    imagem, contexto, biblioteca
                )
                logger.info(
                    "[CNDT] tentativa %d/%d — captcha lido %r (%s)",
                    tentativa,
                    tentativas_captcha,
                    texto,
                    origem,
                )
                # Guarda a imagem de toda tentativa. Se a certidão sair, a
                # lista é descartada; se não sair, é porque TODAS falharam —
                # inclusive as que o sistema achou que tinha lido certo, que
                # são justamente as mais úteis para corrigir a leitura.
                captchas_nao_lidos.append(imagem)

                envio = cliente.post(
                    URL_EMISSAO,
                    data={
                        "gerarCertidaoForm": "gerarCertidaoForm",
                        "gerarCertidaoForm:podeFazerDownload": "false",
                        "gerarCertidaoForm:cpfCnpj": formatar_cnpj(numero),
                        "resposta": texto,
                        "tokenDesafio": token,
                        "emailUsuario": "",
                        "javax.faces.ViewState": estado,
                        "gerarCertidaoForm:btnEmitirCertidao": (
                            "gerarCertidaoForm:btnEmitirCertidao"
                        ),
                        "AJAX:EVENTS_COUNT": "1",
                    },
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                        "Referer": URL_EMISSAO,
                    },
                )
                ultimo_html = envio.text

                liberado = re.search(
                    r'podeFazerDownload"[^>]*value="([^"]*)"', envio.text
                )
                if not (liberado and liberado.group(1).strip().lower() == "true"):
                    visivel = _texto_visivel(envio.text)
                    ultimo_motivo = _motivo_da_recusa(visivel)
                    logger.info("[CNDT] o site não liberou o download: %s", ultimo_motivo)
                    if _e_problema_do_cnpj(visivel):
                        return erro(
                            f"O TST recusou o CNPJ {formatar_cnpj(numero)}: {ultimo_motivo}",
                            salvar_debug(pasta_debug, TIPO, numero, html=envio.text),
                            permanente=True,
                        )
                    continue  # provavelmente o captcha; tenta outro

                # Deu certo: o captcha estava correto. Guarda os caracteres
                # para o sistema ler melhor da próxima vez.
                if origem.startswith("local"):
                    aprendidos = biblioteca.aprender(leitura.glifos, texto)
                    if aprendidos:
                        logger.info(
                            "Aprendi %d caractere(s) novo(s) de captcha.", aprendidos
                        )

                baixado = cliente.get(URL_DOWNLOAD, headers={"Referer": URL_EMISSAO})
                if not parece_pdf(baixado.content):
                    ultimo_html = baixado.text
                    ultimo_motivo = (
                        "o site liberou a emissão, mas /emissaoCertidao não devolveu "
                        "um PDF"
                    )
                    logger.warning("[CNDT] %s", ultimo_motivo)
                    continue

                return _montar_resultado(baixado.content, numero, contexto, regras)

    except httpx.HTTPError as e:
        return erro(
            f"Falha de comunicação com o site do TST: {e}",
            salvar_debug(pasta_debug, TIPO, numero, extra=str(e)),
        )
    except Exception as e:  # pragma: no cover
        logger.exception("Erro inesperado no robô da CNDT")
        return erro(
            f"Erro inesperado no robô da CNDT: {e}",
            salvar_debug(pasta_debug, TIPO, numero, html=ultimo_html, extra=str(e)),
        )

    debug = salvar_debug(pasta_debug, TIPO, numero, html=ultimo_html)
    if guardar_falhas and captchas_nao_lidos:
        guardados = _guardar_captchas(pasta_debug, numero, captchas_nao_lidos)
        if guardados:
            logger.info(
                "[CNDT] Guardei %d imagem(ns) de captcha em logs/debug. "
                "Mande esses arquivos para eu ensinar o sistema a lê-los.",
                guardados,
            )

    if "captcha" in normalizar(ultimo_motivo) or "verificacao" in normalizar(ultimo_motivo):
        return captcha_falhou(
            f"Não consegui ler o captcha do TST em {tentativas_captcha} tentativas. "
            "As imagens que não consegui ler ficaram guardadas em logs/debug "
            "(arquivos que começam com 'captcha_') — me mande esses arquivos que eu "
            "ensino o sistema a ler essas letras, e a partir daí ele acerta sozinho. "
            "Enquanto isso, emita a certidão à mão e anexe o PDF pela tela de Histórico.",
            debug,
        )
    return erro(
        f"Não consegui emitir a CNDT em {tentativas_captcha} tentativas: {ultimo_motivo}",
        debug,
    )


def _guardar_captchas(pasta_debug, cnpj: str, imagens: list[bytes]) -> int:
    """Guarda as imagens de captcha que o sistema não conseguiu ler.

    É o que permite melhorar a leitura: com os arquivos em mãos, dá para
    acrescentar à biblioteca justamente as letras que estão dando trabalho.
    """
    if not pasta_debug:
        return 0

    from datetime import datetime
    from pathlib import Path as _Path

    pasta = _Path(pasta_debug)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    gravados = 0
    for i, imagem in enumerate(imagens, start=1):
        destino = pasta / f"captcha_{carimbo}_{limpar_cnpj(cnpj)}_{i}.png"
        try:
            destino.write_bytes(imagem)
            gravados += 1
        except OSError:
            break
    return gravados


def _motivo_da_recusa(texto_visivel: str) -> str:
    t = normalizar(texto_visivel)
    if "caracteres digitados nao conferem" in t or "codigo invalido" in t:
        return "captcha incorreto"
    if "captcha" in t or "verificacao de seguranca" in t:
        return "captcha recusado"
    if "cnpj" in t and ("invalid" in t or "incorret" in t):
        return "CNPJ recusado pelo site"
    if "nao foi possivel" in t or "erro na emissao" in t:
        return "o site informou erro na emissão"
    if "indisponivel" in t or "manutencao" in t:
        return "site em manutenção ou indisponível"
    return (texto_visivel[:200] or "o site não explicou o motivo").strip()


def _e_problema_do_cnpj(texto_visivel: str) -> bool:
    """Erro do CNPJ não adianta repetir — é dado, não é instabilidade."""
    t = normalizar(texto_visivel)
    return "cnpj" in t and ("invalid" in t or "incorret" in t or "nao encontrado" in t)


def _montar_resultado(
    conteudo: bytes, cnpj: str, contexto: dict, regras: dict
) -> ResultadoConsulta:
    """Salva o PDF e extrai status, número e validade de dentro dele."""
    destino = salvar_pdf(conteudo, contexto["pasta_certidoes"], cnpj, TIPO)
    texto = texto_do_pdf(destino)

    status = _classificar(texto)
    emissao, validade = validade_com_fallback(destino, regras, date.today())

    resultado = ResultadoConsulta(
        status=status,
        caminho_pdf=str(destino),
        data_emissao=emissao,
        data_validade=validade,
        numero_certidao=_numero_da_certidao(texto),
    )
    logger.info(
        "[CNDT] %s emitida: %s (validade %s)", formatar_cnpj(cnpj), status, validade
    )
    return resultado
