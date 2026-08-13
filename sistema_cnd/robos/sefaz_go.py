"""Certidão de Débito em Dívida Ativa — SEFAZ Goiás.

Emissão:   https://www.sefaz.go.gov.br/Certidao/Emissao/  (default.asp)
Validação: https://www.sefaz.go.gov.br/Certidao/Validacao/001frmValidaCertidao_c.asp

O QUE FOI DESCOBERTO SOBRE O SITE (agosto/2026)
-----------------------------------------------
1. O endereço do levantamento (001frmEmiteCertidao_c.asp) REDIRECIONA para
   default.asp. O robô já aponta direto para o endereço final.

2. NÃO tem captcha — confirmado. Por isso este robô usa requisição direta
   (httpx), sem abrir navegador: é muito mais rápido e mais leve para o
   servidor da SEFAZ.

3. Existe uma ETAPA DE CONFIRMAÇÃO que o levantamento não mencionava, e ela
   só aparece às vezes:
     - CNPJ cadastrado em Goiás  -> o site devolve uma tela perguntando
       "Confirma o Nome do Contribuinte: FULANO?" e só emite o PDF depois de
       um segundo envio com ConfirmaNomeContribuinte=Sim;
     - CNPJ não cadastrado em GO -> o site devolve o PDF direto, no primeiro
       envio, com certidão negativa.
   O robô trata os dois caminhos.

4. A VALIDADE NÃO É DE ~60 DIAS. Os PDFs emitidos dizem, em texto,
   "Certidao VALIDA POR 120 DIAS" — e não imprimem nenhuma data em número.
   A data de emissão vem só por extenso ("GOIANIA, 13 AGOSTO DE 2026").
   Por isso a validade é calculada como emissão + prazo lido do próprio PDF.
   O número do config.yaml continua sendo apenas plano B.

5. A certidão vale "para a matriz e suas filiais", conforme o próprio texto.

Regra do projeto respeitada: certidão POSITIVA não é erro. O PDF é salvo do
mesmo jeito, com status POSITIVA, e o painel mostra em vermelho para você agir.
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

logger = logging.getLogger(__name__)

TIPO = "ESTADUAL_GO"
BASE = "https://www.sefaz.go.gov.br/Certidao/Emissao/"
URL_FORMULARIO = BASE + "default.asp"
URL_EMISSAO = BASE + "certidao.asp"
URL_VALIDACAO = (
    "https://www.sefaz.go.gov.br/Certidao/Validacao/001frmValidaCertidao_c.asp"
)

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

#: O site é ASP antigo e responde em Windows-1252, não em UTF-8.
CODIFICACAO = "cp1252"

#: Tipo de certidão no formulário. Hoje só existe "01" = Dívida Ativa.
TIPO_DIVIDA_ATIVA = "01"
#: 1 = CPF, 2 = CNPJ.
DOCUMENTO_CNPJ = "2"


# =============================================================================
#  AUXILIARES
# =============================================================================


def _decodificar(conteudo: bytes) -> str:
    return conteudo.decode(CODIFICACAO, errors="replace")


def _texto_visivel(html: str) -> str:
    limpo = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    limpo = re.sub(r"<[^>]+>", " ", limpo)
    return re.sub(r"\s+", " ", limpo).strip()


def _campos_ocultos(html: str) -> dict[str, str]:
    """Todos os <input type=hidden> — é o que a tela de confirmação devolve."""
    campos: dict[str, str] = {}
    for tag in re.findall(r'<input[^>]*type=["\']?hidden["\']?[^>]*>', html, re.I):
        nome = re.search(r'name=["\']([^"\']+)["\']', tag, re.I)
        valor = re.search(r'value=["\']([^"\']*)["\']', tag, re.I)
        if nome:
            campos[nome.group(1)] = valor.group(1) if valor else ""
    return campos


def _nome_confirmado(html: str) -> str | None:
    achado = re.search(r"Contribuinte:\s*<strong>(.*?)</strong>", html, re.S | re.I)
    return achado.group(1).strip() if achado else None


def _pede_confirmacao(html: str) -> bool:
    return "confirmanomecontribuinte" in html.lower()


def _classificar(texto_pdf: str) -> str:
    """Decide o status pelo texto do PDF.

    Textos reais observados nos documentos emitidos:
      "CERTIDAO DE DEBITO INSCRITO EM DIVIDA ATIVA - NEGATIVA" + "NAO CONSTA DEBITO"
      "CERTIDAO DE DEBITO EM DIVIDA ATIVA - POSITIVA COM EFEITO NEGATIVO
       (SUSPENSAO DA EXIGIBILIDADE)"
    """
    t = normalizar(texto_pdf)

    if "positiva com efeito negativo" in t or "efeito negativo" in t:
        return STATUS_POSITIVA_COM_EFEITO_NEGATIVA
    if "nao consta debito" in t or "- negativa" in t or "ativa - negativa" in t:
        return STATUS_NEGATIVA
    if "positiva" in t:
        return STATUS_POSITIVA

    logger.warning(
        "Não reconheci o tipo da certidão da SEFAZ-GO pelo texto; assumindo negativa."
    )
    return STATUS_NEGATIVA


def _numero_da_certidao(texto_pdf: str) -> str | None:
    """Lê 'NR. CERTIDÃO: Nº 73209981'."""
    for padrao in (
        r"NR\.?\s*CERTID[ÃA]O\s*:?\s*N?[º°o]?\s*([\w.-]{4,30})",
        r"CERTID[ÃA]O\s*N?[º°o]\s*([\w.-]{4,30})",
    ):
        achado = re.search(padrao, texto_pdf or "", re.I)
        if achado:
            return achado.group(1).strip(" .")
    return None


def _validador(texto_pdf: str) -> str | None:
    """Código com que dá para conferir a certidão no site da SEFAZ."""
    achado = re.search(r"VALIDADOR\s*:?\s*([\d.]{8,30})", texto_pdf or "", re.I)
    return achado.group(1).strip(" .") if achado else None


def _motivo_da_recusa(texto_visivel: str) -> str:
    t = normalizar(texto_visivel)
    if "nao encontrado" in t or "nao cadastrado" in t or "inexistente" in t:
        return "CNPJ não encontrado na base da SEFAZ-GO"
    if "invalid" in t:
        return "o site considerou o CNPJ inválido"
    if "indisponivel" in t or "manutencao" in t or "fora do ar" in t:
        return "site da SEFAZ-GO em manutenção ou indisponível"
    if "acesso negado" in t:
        return "acesso negado pelo site da SEFAZ-GO"
    return (texto_visivel[:200] or "o site não explicou o motivo").strip()


# =============================================================================
#  ROBÔ
# =============================================================================


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão estadual de Goiás do CNPJ informado."""
    numero = limpar_cnpj(cnpj)
    if len(numero) != 14:
        return erro(f"CNPJ inválido para a SEFAZ-GO: {cnpj!r}", permanente=True)

    pasta_debug = contexto.get("pasta_debug")
    regras = contexto.get("regras") or {}
    # A opção "para fins de espólio" fica sempre NÃO, conforme combinado.
    espolio = "S" if regras.get("fins_espolio") else "N"

    formulario = {
        "Certidao.Tipo": TIPO_DIVIDA_ATIVA,
        "Certidao.TipoDocumento": DOCUMENTO_CNPJ,
        "Certidao.NumeroDocumento": "",
        "Certidao.NumeroDocumentoCNPJ": formatar_cnpj(numero),
        "Certidao.Espolio": espolio,
    }

    try:
        with httpx.Client(
            headers=CABECALHOS, timeout=90, follow_redirects=True
        ) as cliente:
            # 1) Abre o formulário só para pegar os cookies da sessão ASP.
            cliente.get(URL_FORMULARIO)

            # 2) Envia o CNPJ.
            resposta = cliente.post(
                URL_EMISSAO, data=formulario, headers={"Referer": URL_FORMULARIO}
            )

            # Caminho curto: CNPJ sem cadastro em GO já devolve o PDF aqui.
            if parece_pdf(resposta.content):
                logger.info("[SEFAZ-GO] PDF veio direto, sem tela de confirmação.")
                return _montar_resultado(resposta.content, numero, contexto, regras)

            html = _decodificar(resposta.content)

            # Caminho longo: o site pede confirmação do nome do contribuinte.
            if _pede_confirmacao(html):
                nome = _nome_confirmado(html)
                logger.info("[SEFAZ-GO] Site pediu confirmação do nome: %s", nome)

                campos = _campos_ocultos(html) or dict(formulario)
                campos["Certidao.ConfirmaNomeContribuinte"] = "Sim"

                resposta = cliente.post(
                    URL_EMISSAO, data=campos, headers={"Referer": URL_EMISSAO}
                )

                if parece_pdf(resposta.content):
                    return _montar_resultado(
                        resposta.content, numero, contexto, regras, nome
                    )

                html = _decodificar(resposta.content)
                return erro(
                    "A SEFAZ-GO aceitou a confirmação do nome mas não devolveu o PDF: "
                    + _motivo_da_recusa(_texto_visivel(html)),
                    salvar_debug(pasta_debug, TIPO, numero, html=html),
                )

            # Nem PDF nem confirmação: o site recusou por algum motivo.
            visivel = _texto_visivel(html)
            motivo = _motivo_da_recusa(visivel)
            debug = salvar_debug(pasta_debug, TIPO, numero, html=html)

            if "não encontrado" in motivo or "inválido" in motivo:
                return erro(
                    f"A SEFAZ-GO recusou o CNPJ {formatar_cnpj(numero)}: {motivo}",
                    debug,
                    permanente=True,
                )
            return erro(f"A SEFAZ-GO não emitiu a certidão: {motivo}", debug)

    except httpx.HTTPError as e:
        return erro(
            f"Falha de comunicação com o site da SEFAZ-GO: {e}",
            salvar_debug(pasta_debug, TIPO, numero, extra=str(e)),
        )
    except Exception as e:  # pragma: no cover
        logger.exception("Erro inesperado no robô da SEFAZ-GO")
        return erro(
            f"Erro inesperado no robô da SEFAZ-GO: {e}",
            salvar_debug(pasta_debug, TIPO, numero, extra=str(e)),
        )


def _montar_resultado(
    conteudo: bytes,
    cnpj: str,
    contexto: dict,
    regras: dict,
    razao_social: str | None = None,
) -> ResultadoConsulta:
    """Salva o PDF e lê status, número e validade de dentro dele."""
    destino = salvar_pdf(conteudo, contexto["pasta_certidoes"], cnpj, TIPO)
    texto = texto_do_pdf(destino)

    status = _classificar(texto)
    # A validade sai de "VALIDA POR N DIAS" + data de emissão por extenso;
    # o prazo do config.yaml só entra se a leitura do PDF falhar.
    emissao, validade = validade_com_fallback(destino, regras, date.today())

    resultado = ResultadoConsulta(
        status=status,
        caminho_pdf=str(destino),
        data_emissao=emissao,
        data_validade=validade,
        numero_certidao=_numero_da_certidao(texto),
    )
    resultado.detalhes["validador"] = _validador(texto)
    if razao_social:
        resultado.detalhes["razao_social_no_site"] = razao_social

    if status == STATUS_POSITIVA:
        logger.warning(
            "[SEFAZ-GO] %s tem DÉBITO EM DÍVIDA ATIVA — certidão positiva emitida "
            "e guardada assim mesmo.",
            formatar_cnpj(cnpj),
        )
    logger.info(
        "[SEFAZ-GO] %s: %s (emissão %s, validade %s)",
        formatar_cnpj(cnpj),
        status,
        emissao,
        validade,
    )
    return resultado
