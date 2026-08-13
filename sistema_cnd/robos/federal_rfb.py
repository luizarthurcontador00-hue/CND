"""Certidão Conjunta Federal — Receita Federal / PGFN (inclui INSS).

O QUE FOI DESCOBERTO SOBRE O SITE (agosto/2026)
-----------------------------------------------
1. OS DOIS ENDEREÇOS DO LEVANTAMENTO SAÍRAM DO AR (404):
     solucoes.receita.fazenda.gov.br/Servicos/certidaointernet/PJ/Emitir
     servicos.receita.fazenda.gov.br/Servicos/certidao/CndConjuntaInter/...
   A Receita migrou o serviço para um portal novo:
     https://servicos.receitafederal.gov.br/servico/certidoes

2. O PORTAL NOVO É UMA APLICAÇÃO ANGULAR (SPA). A página não traz formulário
   nenhum no HTML: tudo é montado por JavaScript. Por isso este robô abre
   navegador de verdade (Playwright) — requisição direta não funcionaria.

3. TEM hCAPTCHA (js.hcaptcha.com), o mesmo tipo usado pela Caixa. Não existe
   jeito de resolver isso na própria máquina: ou há um serviço pago
   configurado no config.yaml, ou a certidão precisa ser emitida à mão.
   O sistema NUNCA trava por isso — registra CAPTCHA_FALHOU e você anexa o PDF
   pela tela de Histórico.

4. AS ROTAS DA APLICAÇÃO, lidas do próprio pacote JavaScript do portal:
     /servico/certidoes/emitir     -> emitir certidão nova
     /servico/certidoes/consultar  -> consultar certidão já emitida e tirar 2ª via
   O campo do documento se chama "niContribuinte" (placeholder "Informe o CNPJ")
   e existe um seletor de tipo de contribuinte (CPF/CNPJ/CIB/CNO).

REGRA ESPECIAL DA 2ª VIA (a mais importante deste robô)
-------------------------------------------------------
Quando a empresa tem "positiva com efeitos de negativa", o portal NÃO emite uma
certidão nova. O próprio portal diz, em texto:

    "Emita novas certidões ou consulte certidões emitidas a partir de
     22/01/2018 e emita 2ª via."

Ou seja: a saída é a rota /consultar. Por isso, quando a emissão nova é
recusada, este robô NÃO devolve erro na hora — ele tenta primeiro recuperar a
2ª via de uma certidão anterior que ainda esteja válida (os dados vêm em
contexto["emissao_anterior"]). Só desiste se isso também falhar.

OUTRAS REGRAS
-------------
- Emitida pelo CNPJ da MATRIZ e vale para as filiais. Quem troca o CNPJ da
  filial pelo da matriz é o orquestrador (config.yaml -> usa_cnpj_matriz).
- Validade de 180 dias, mas sempre lida do PDF quando possível.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from robos.base import (
    STATUS_NEGATIVA,
    STATUS_POSITIVA,
    STATUS_POSITIVA_COM_EFEITO_NEGATIVA,
    NavegadorIndisponivel,
    ResultadoConsulta,
    abrir_navegador,
    capturar_estado,
    captcha_falhou,
    erro,
    fechar_navegador,
    formatar_cnpj,
    limpar_cnpj,
    normalizar,
    parece_pdf,
    salvar_debug,
    salvar_pdf,
    sem_pendencia_nao_emitiu,
    texto_do_pdf,
    validade_com_fallback,
)
from robos.captcha import resolver_hcaptcha

logger = logging.getLogger(__name__)

TIPO = "FEDERAL"
BASE = "https://servicos.receitafederal.gov.br/servico/certidoes"
URL_EMITIR = f"{BASE}/emitir"
URL_CONSULTAR = f"{BASE}/consultar"

#: Campo do documento no formulário Angular do portal.
SELETOR_DOCUMENTO = (
    '[formcontrolname="niContribuinte"], input[placeholder*="CNPJ" i], '
    'input[name="niContribuinte"]'
)
PISTAS_BOTAO_ENVIAR = ("emitir", "consultar", "continuar", "avançar", "avancar", "enviar")
PISTAS_BOTAO_PDF = ("pdf", "imprimir", "baixar", "salvar", "download", "2ª via", "2a via", "segunda via")

#: Frases do portal que indicam que a emissão nova foi recusada e o caminho é
#: a segunda via de uma certidão anterior.
PISTAS_USE_SEGUNDA_VIA = (
    "ja possui certidao",
    "já possui certidão",
    "consulte certidoes emitidas",
    "emita 2a via",
    "emita 2ª via",
    "segunda via",
    "certidao vigente",
    "certidão vigente",
    "nao e possivel emitir",
    "não é possível emitir",
)


# =============================================================================
#  AUXILIARES
# =============================================================================


def _visivel(elemento) -> bool:
    try:
        return elemento.is_visible()
    except Exception:
        return False


def _achar_botao(pagina, pistas):
    for elemento in pagina.query_selector_all("button, input[type=submit], a"):
        if not _visivel(elemento):
            continue
        try:
            rotulo = normalizar(
                (elemento.inner_text() or "")
                + " "
                + (elemento.get_attribute("value") or "")
                + " "
                + (elemento.get_attribute("aria-label") or "")
            )
        except Exception:
            continue
        if any(pista in rotulo for pista in pistas):
            return elemento
    return None


def _classificar(texto: str) -> str:
    """Decide o status pelo texto da certidão federal."""
    t = normalizar(texto)
    if "positiva com efeito" in t or "efeitos de negativa" in t or "efeito de negativa" in t:
        return STATUS_POSITIVA_COM_EFEITO_NEGATIVA
    if "certidao negativa" in t or "nao constam pendencias" in t or "nao ha pendencias" in t:
        return STATUS_NEGATIVA
    if "certidao positiva" in t or "constam pendencias" in t:
        return STATUS_POSITIVA
    logger.warning("Não reconheci o tipo da certidão federal pelo texto; assumindo negativa.")
    return STATUS_NEGATIVA


def _numero_da_certidao(texto: str) -> str | None:
    for padrao in (
        r"c[oó]digo\s+de\s+controle\s+da\s+certid[ãa]o\s*:?\s*([\w.\-]{6,40})",
        r"certid[ãa]o\s*n[º°.:]*\s*([\w./-]{6,40})",
    ):
        achado = re.search(padrao, texto or "", re.I)
        if achado:
            return achado.group(1).strip(" .")
    return None


def _sitekey_hcaptcha(pagina) -> str | None:
    """Descobre a chave pública do hCaptcha, exigida pelo serviço de resolução."""
    for seletor in ("[data-sitekey]", ".h-captcha", "iframe[src*='hcaptcha']"):
        elemento = pagina.query_selector(seletor)
        if not elemento:
            continue
        chave = elemento.get_attribute("data-sitekey")
        if chave:
            return chave
        origem = elemento.get_attribute("src") or ""
        achado = re.search(r"sitekey=([0-9a-f-]{10,})", origem)
        if achado:
            return achado.group(1)
    return None


def _tem_hcaptcha(pagina) -> bool:
    try:
        return bool(
            pagina.query_selector(".h-captcha, [data-sitekey], iframe[src*='hcaptcha']")
        )
    except Exception:
        return False


def _responder_hcaptcha(pagina, contexto: dict) -> bool:
    """Tenta resolver o hCaptcha usando o serviço pago, se houver.

    Devolve True quando o token foi injetado na página. Sem serviço configurado
    devolve False — e o robô então registra CAPTCHA_FALHOU, sem travar nada.
    """
    cfg = contexto.get("config")
    if cfg is None or not cfg.captcha_disponivel:
        return False

    sitekey = _sitekey_hcaptcha(pagina)
    if not sitekey:
        logger.info("[FEDERAL] Não achei a chave do hCaptcha na página.")
        return False

    logger.info("[FEDERAL] Pedindo a resolução do hCaptcha ao serviço configurado…")
    token = resolver_hcaptcha(sitekey, pagina.url, cfg)
    if not token:
        return False

    # O widget guarda a resposta em textareas escondidas; preencher as duas é o
    # jeito usual de o formulário aceitar o token.
    pagina.evaluate(
        """(token) => {
            for (const nome of ['h-captcha-response', 'g-recaptcha-response']) {
                document.querySelectorAll(`[name="${nome}"]`).forEach(campo => {
                    campo.value = token;
                    campo.dispatchEvent(new Event('input', {bubbles: true}));
                    campo.dispatchEvent(new Event('change', {bubbles: true}));
                });
            }
        }""",
        token,
    )
    logger.info("[FEDERAL] hCaptcha resolvido pelo serviço.")
    return True


def _mensagem_captcha(contexto: dict) -> str:
    cfg = contexto.get("config")
    if cfg is not None and cfg.captcha_disponivel:
        return (
            "O serviço de captcha configurado não conseguiu resolver o hCaptcha do portal "
            "da Receita desta vez. Tente de novo mais tarde ou emita a certidão à mão e "
            "anexe o PDF pela tela de Histórico."
        )
    return (
        "O portal da Receita Federal exige hCaptcha (o \"não sou um robô\"), que não tem "
        "como ser resolvido na própria máquina. Duas saídas: emitir a certidão à mão no "
        "site e anexar o PDF pela tela de Histórico (é o caminho recomendado, sem custo), "
        "ou contratar um serviço de captcha e preencher captcha.provedor e captcha.chave_api "
        "no config.yaml para automatizar."
    )


def _arquivo_sessao(contexto: dict) -> Path | None:
    caminho = contexto.get("arquivo_modelos_captcha")
    return Path(caminho).parent / "sessao_federal.json" if caminho else None


# =============================================================================
#  ROBÔ
# =============================================================================


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão conjunta federal do CNPJ da matriz."""
    numero = limpar_cnpj(cnpj)
    if len(numero) != 14:
        return erro(f"CNPJ inválido para a certidão federal: {cnpj!r}", permanente=True)

    pasta_debug = contexto.get("pasta_debug")
    regras = contexto.get("regras") or {}
    sessao = _arquivo_sessao(contexto)

    pw = navegador = pagina = None
    try:
        pw, navegador, pagina = abrir_navegador(contexto, estado=sessao)
    except NavegadorIndisponivel as e:
        return erro(str(e), permanente=True)
    except Exception as e:
        return erro(f"Não consegui abrir o navegador para a certidão federal: {e}")

    try:
        # ------------------------------------------------- 1) emissão nova
        resultado = _tentar_emitir(pagina, numero, contexto, regras, pasta_debug)
        if resultado is not None:
            return resultado

        # ------------------------------------- 2) regra especial da 2ª via
        anterior = contexto.get("emissao_anterior")
        logger.info(
            "[FEDERAL] O portal não emitiu certidão nova. Tentando a 2ª via de uma "
            "certidão anterior ainda válida."
        )
        return _tentar_segunda_via(
            pagina, numero, contexto, regras, pasta_debug, anterior
        )

    except Exception as e:
        logger.exception("Erro inesperado no robô da Receita Federal")
        html = print_tela = None
        if pagina is not None:
            html, print_tela = capturar_estado(pagina)
        return erro(
            f"Erro inesperado no robô da certidão federal: {e}",
            salvar_debug(
                pasta_debug, TIPO, numero, html=html, screenshot=print_tela, extra=str(e)
            ),
        )
    finally:
        fechar_navegador(pw, navegador)


def _preencher_documento(pagina, numero: str) -> bool:
    """Escolhe o tipo CNPJ e preenche o número. True se conseguiu."""
    # O portal tem um seletor de tipo (CPF/CNPJ/CIB/CNO). Marca o de CNPJ.
    for elemento in pagina.query_selector_all("input[type=radio], button, label, option"):
        if not _visivel(elemento):
            continue
        try:
            rotulo = normalizar(
                (elemento.inner_text() or "")
                + " "
                + (elemento.get_attribute("value") or "")
                + " "
                + (elemento.get_attribute("aria-label") or "")
            )
        except Exception:
            continue
        if rotulo.strip() == "cnpj":
            try:
                elemento.click()
                pagina.wait_for_timeout(500)
            except Exception:
                pass
            break

    campo = pagina.query_selector(SELETOR_DOCUMENTO)
    if campo is None:
        return False
    campo.fill(numero)
    return True


def _tentar_emitir(pagina, numero, contexto, regras, pasta_debug):
    """Tenta a emissão nova. Devolve ResultadoConsulta, ou None para cair na 2ª via."""
    pagina.goto(URL_EMITIR, wait_until="networkidle")

    if not _preencher_documento(pagina, numero):
        html, print_tela = capturar_estado(pagina)
        return erro(
            "Não encontrei o campo do CNPJ no portal da Receita — o site provavelmente "
            "mudou. Os arquivos de depuração estão na tela de Logs.",
            salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
        )

    if _tem_hcaptcha(pagina) and not _responder_hcaptcha(pagina, contexto):
        html, print_tela = capturar_estado(pagina)
        return captcha_falhou(
            _mensagem_captcha(contexto),
            salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
        )

    botao = _achar_botao(pagina, PISTAS_BOTAO_ENVIAR)
    if botao is None:
        html, print_tela = capturar_estado(pagina)
        return erro(
            "Não encontrei o botão de emitir no portal da Receita.",
            salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
        )
    botao.click()
    pagina.wait_for_load_state("networkidle")

    texto_pagina = pagina.inner_text("body")
    t = normalizar(texto_pagina)

    # O portal recusou a emissão nova: o caminho passa a ser a 2ª via.
    if any(pista in t for pista in PISTAS_USE_SEGUNDA_VIA):
        return None

    conteudo = _baixar_pdf(pagina)
    if not parece_pdf(conteudo):
        # Não achou PDF, mas também não pediu 2ª via: tenta a 2ª via assim mesmo,
        # que é a situação da "positiva com efeitos de negativa".
        logger.info("[FEDERAL] Não veio PDF na emissão; vou tentar a 2ª via.")
        return None

    return _montar_resultado(conteudo, numero, contexto, regras, texto_pagina)


def _tentar_segunda_via(pagina, numero, contexto, regras, pasta_debug, anterior):
    """Recupera a 2ª via de uma certidão anterior ainda válida."""
    pagina.goto(URL_CONSULTAR, wait_until="networkidle")

    if not _preencher_documento(pagina, numero):
        html, print_tela = capturar_estado(pagina)
        return erro(
            "Não encontrei o campo do CNPJ na tela de consulta da Receita.",
            salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
        )

    if _tem_hcaptcha(pagina) and not _responder_hcaptcha(pagina, contexto):
        html, print_tela = capturar_estado(pagina)
        return captcha_falhou(
            _mensagem_captcha(contexto),
            salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
        )

    botao = _achar_botao(pagina, PISTAS_BOTAO_ENVIAR)
    if botao is not None:
        botao.click()
        pagina.wait_for_load_state("networkidle")

    conteudo = _baixar_pdf(pagina)
    texto_pagina = pagina.inner_text("body")

    if parece_pdf(conteudo):
        logger.info("[FEDERAL] 2ª via recuperada com sucesso.")
        resultado = _montar_resultado(conteudo, numero, contexto, regras, texto_pagina)
        resultado.detalhes["origem"] = "segunda_via"
        return resultado

    html, print_tela = capturar_estado(pagina)
    debug = salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela)

    validade_anterior = (anterior or {}).get("data_validade")
    if validade_anterior and validade_anterior >= date.today():
        return sem_pendencia_nao_emitiu(
            "O portal da Receita não emitiu certidão nova nem devolveu a 2ª via, mas há "
            f"uma certidão anterior válida até {validade_anterior.strftime('%d/%m/%Y')} "
            "guardada no histórico. Confira no portal e, se precisar, anexe o PDF à mão.",
            debug,
        )

    return sem_pendencia_nao_emitiu(
        "O portal da Receita não emitiu certidão nova e não encontrei 2ª via de certidão "
        "anterior válida. Isso costuma acontecer com 'positiva com efeitos de negativa' "
        "cuja certidão anterior já venceu. Emita à mão e anexe pela tela de Histórico.",
        debug,
    )


def _baixar_pdf(pagina) -> bytes | None:
    """Obtém o PDF da certidão, seja por download, nova aba ou impressão."""
    botao = _achar_botao(pagina, PISTAS_BOTAO_PDF)

    if botao is not None:
        try:
            with pagina.expect_download(timeout=25000) as espera:
                botao.click()
            caminho = espera.value.path()
            if caminho:
                return Path(caminho).read_bytes()
        except Exception:
            pass

        try:
            with pagina.context.expect_page(timeout=15000) as espera_aba:
                botao.click()
            aba = espera_aba.value
            aba.wait_for_load_state("networkidle")
            conteudo = _pdf_da_pagina(aba)
            aba.close()
            if conteudo:
                return conteudo
        except Exception:
            pass

    return _pdf_da_pagina(pagina)


def _pdf_da_pagina(pagina) -> bytes | None:
    """Gera PDF da página (só funciona com o navegador invisível)."""
    try:
        return pagina.pdf(format="A4", print_background=True)
    except Exception as e:
        logger.info("Não consegui gerar o PDF da página da Receita: %s", e)
        return None


def _montar_resultado(conteudo, cnpj, contexto, regras, texto_pagina=""):
    destino = salvar_pdf(conteudo, contexto["pasta_certidoes"], cnpj, TIPO)
    texto_pdf = texto_do_pdf(destino)

    status = _classificar(texto_pdf or texto_pagina)
    emissao, validade = validade_com_fallback(destino, regras, date.today())

    logger.info(
        "[FEDERAL] %s: %s (validade %s)", formatar_cnpj(cnpj), status, validade
    )
    return ResultadoConsulta(
        status=status,
        caminho_pdf=str(destino),
        data_emissao=emissao,
        data_validade=validade,
        numero_certidao=_numero_da_certidao(texto_pdf) or _numero_da_certidao(texto_pagina),
    )
