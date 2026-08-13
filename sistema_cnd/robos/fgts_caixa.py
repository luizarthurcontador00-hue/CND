"""Certificado de Regularidade do FGTS — CRF / Caixa Econômica Federal.

Site: https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf

O QUE FOI DESCOBERTO SOBRE O SITE (agosto/2026)
-----------------------------------------------
Este é, de longe, o site mais defendido dos quatro. Antes de qualquer captcha,
o domínio inteiro da Caixa fica atrás do **ShieldSquare / PerfDrive (Radware
Bot Manager)**, um serviço comercial anti-robô. Quando ele desconfia do
visitante, desvia a navegação para validate.perfdrive.com e exige um hCaptcha
("toque no quadro para verificar que você não é robô").

Consequências práticas, que moldaram este robô:

1. NÃO DÁ PARA USAR REQUISIÇÃO DIRETA (httpx). O ShieldSquare avalia
   impressão digital do navegador — TLS, JavaScript, canvas. Um cliente HTTP
   simples é barrado sempre. Este é o caso em que o navegador de verdade
   (Playwright) é obrigatório, ao contrário da CNDT e da SEFAZ-GO.

2. O bloqueio depende muito do ENDEREÇO DE REDE. Endereços de servidor/nuvem
   são barrados quase sempre; a conexão comum de um escritório costuma passar
   sem nem ver o desafio. Ou seja: o comportamento na sua máquina tende a ser
   bem melhor do que em qualquer teste feito fora dela.

3. QUANDO O DESAFIO APARECE, ELE É RESOLVIDO UMA VEZ SÓ. Depois de validado, o
   ShieldSquare entrega um cookie de liberação. Por isso este robô GUARDA A
   SESSÃO em dados/sessao_fgts.json e reaproveita nas próximas emissões — você
   resolve o desafio uma vez e o robô emite várias certidões em seguida.
   Para resolver à mão, ponha navegador.headless: false no config.yaml.

REGRAS DO CRF (do levantamento, mantidas)
-----------------------------------------
- CNPJ completo, SÓ NÚMEROS, e o campo UF fica EM BRANCO. A UF só é usada em
  consulta pelo CNPJ básico (8 dígitos).
- Resultado é "Regular" ou "Irregular".
- Validade de 30 dias, renovável apenas A PARTIR DO 10º DIA ANTERIOR ao
  vencimento. Quem respeita essa janela é o agendador
  (config.yaml -> certidoes.FGTS.janela_renovacao_dias), não este arquivo.

SOBRE OS SELETORES
------------------
Não foi possível abrir o formulário real durante o desenvolvimento (o IP usado
era barrado pelo anti-robô). Para não depender de nomes de campo adivinhados,
este robô PROCURA os elementos pelo que eles são — um campo de texto cujo
rótulo/id/nome fale em CNPJ, um botão cujo texto seja "Consultar" — em vez de
usar identificadores fixos que provavelmente estariam errados. Se mesmo assim
não encontrar, ele salva print + HTML da página em logs/debug para ajuste.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from robos.base import (
    STATUS_NEGATIVA,
    STATUS_POSITIVA,
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
    texto_do_pdf,
    validade_com_fallback,
)

logger = logging.getLogger(__name__)

TIPO = "FGTS"
URL_CONSULTA = "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf"

#: Marcas que denunciam a parede do anti-robô.
MARCAS_ANTIBOT = (
    "perfdrive.com",
    "shieldsquare",
    "hcaptcha",
    "verificacao de seguranca",
    "nao e robo",
    "não é robô",
)

#: Como o campo do CNPJ costuma se identificar. A busca é por qualquer um.
PISTAS_CAMPO_CNPJ = ("cnpj", "inscricao", "inscrição", "empregador", "documento")
#: Texto do botão que dispara a consulta.
PISTAS_BOTAO_CONSULTAR = ("consultar", "pesquisar", "buscar")
#: Texto do botão/link que abre o certificado.
PISTAS_BOTAO_IMPRIMIR = ("imprimir", "visualizar", "certificado", "crf", "emitir")


# =============================================================================
#  AUXILIARES
# =============================================================================


def _e_parede_antibot(url: str, html: str) -> bool:
    alvo = normalizar((url or "") + " " + (html or ""))
    return any(marca in alvo for marca in MARCAS_ANTIBOT)


def _situacao_do_texto(texto: str) -> str | None:
    """Lê 'Regular' ou 'Irregular' no resultado da consulta.

    A ordem importa: "irregular" contém "regular", então o irregular é testado
    primeiro.
    """
    t = normalizar(texto)
    if re.search(r"\birregular\b", t) or "nao possui certificado" in t:
        return "IRREGULAR"
    if re.search(r"\bregular\b", t):
        return "REGULAR"
    return None


def _numero_do_crf(texto: str) -> str | None:
    for padrao in (
        r"n[uú]mero\s+do\s+crf\s*:?\s*([\w.\-/]{6,40})",
        r"certificado\s*n[º°.:]*\s*([\w.\-/]{6,40})",
    ):
        achado = re.search(padrao, texto or "", re.I)
        if achado:
            return achado.group(1).strip(" .")
    return None


def _achar_campo_cnpj(pagina):
    """Procura o campo do CNPJ pelo que ele é, não por um id fixo."""
    for campo in pagina.query_selector_all("input[type=text], input:not([type])"):
        try:
            if not campo.is_visible():
                continue
            assinatura = normalizar(
                " ".join(
                    filter(
                        None,
                        [
                            campo.get_attribute("id") or "",
                            campo.get_attribute("name") or "",
                            campo.get_attribute("title") or "",
                            campo.get_attribute("placeholder") or "",
                            campo.get_attribute("aria-label") or "",
                        ],
                    )
                )
            )
            if any(pista in assinatura for pista in PISTAS_CAMPO_CNPJ):
                return campo
        except Exception:
            continue

    # Nenhuma pista bateu: se só existe um campo de texto visível, é ele.
    visiveis = [
        c
        for c in pagina.query_selector_all("input[type=text], input:not([type])")
        if _visivel(c)
    ]
    return visiveis[0] if len(visiveis) == 1 else None


def _visivel(elemento) -> bool:
    try:
        return elemento.is_visible()
    except Exception:
        return False


def _achar_botao(pagina, pistas) -> object | None:
    """Botão/link cujo texto ou value bata com alguma das pistas."""
    seletores = "button, input[type=submit], input[type=button], a"
    for elemento in pagina.query_selector_all(seletores):
        if not _visivel(elemento):
            continue
        try:
            rotulo = normalizar(
                (elemento.inner_text() or "")
                + " "
                + (elemento.get_attribute("value") or "")
                + " "
                + (elemento.get_attribute("title") or "")
            )
        except Exception:
            continue
        if any(pista in rotulo for pista in pistas):
            return elemento
    return None


def _arquivo_sessao(contexto: dict) -> Path | None:
    """Onde guardar os cookies do ShieldSquare entre execuções."""
    caminho = contexto.get("arquivo_modelos_captcha")
    if not caminho:
        return None
    return Path(caminho).parent / "sessao_fgts.json"


def _mensagem_antibot(headless: bool) -> str:
    if headless:
        return (
            "O site da Caixa apresentou a verificação anti-robô (ShieldSquare/hCaptcha) "
            "e o navegador está em modo invisível, então não há como respondê-la. "
            "Para resolver uma vez e liberar as próximas emissões: abra o config.yaml, "
            "mude navegador.headless para false, rode a consulta do FGTS e resolva o "
            "desafio na tela. A sessão liberada fica guardada e é reaproveitada. "
            "Se preferir, emita o CRF à mão no site e anexe o PDF pela tela de Histórico."
        )
    return (
        "O site da Caixa apresentou a verificação anti-robô (ShieldSquare/hCaptcha) e ela "
        "não foi respondida a tempo na janela do navegador. Rode de novo e resolva o "
        "desafio, ou emita o CRF à mão e anexe o PDF pela tela de Histórico."
    )


# =============================================================================
#  ROBÔ
# =============================================================================


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite o CRF/FGTS do CNPJ informado."""
    numero = limpar_cnpj(cnpj)
    if len(numero) != 14:
        return erro(f"CNPJ inválido para o FGTS: {cnpj!r}", permanente=True)

    pasta_debug = contexto.get("pasta_debug")
    regras = contexto.get("regras") or {}
    headless = bool(contexto.get("headless", True))
    sessao = _arquivo_sessao(contexto)
    # Com o navegador visível, dá tempo de a pessoa resolver o desafio.
    espera_desafio = 0 if headless else int(regras.get("segundos_para_resolver", 180))

    pw = navegador = pagina = None
    try:
        pw, navegador, pagina = abrir_navegador(contexto, estado=sessao)
    except NavegadorIndisponivel as e:
        return erro(str(e), permanente=True)
    except Exception as e:
        return erro(f"Não consegui abrir o navegador para o FGTS: {e}")

    try:
        pagina.goto(URL_CONSULTA, wait_until="domcontentloaded")

        # ------------------------------------------------ parede do anti-robô
        if _e_parede_antibot(pagina.url, pagina.content()):
            logger.warning("[FGTS] O site da Caixa mostrou a verificação anti-robô.")
            if espera_desafio:
                logger.info(
                    "[FGTS] Resolva o desafio na janela do navegador. "
                    "Aguardando até %d segundos.",
                    espera_desafio,
                )
                try:
                    pagina.wait_for_function(
                        "() => !location.host.includes('perfdrive')",
                        timeout=espera_desafio * 1000,
                    )
                except Exception:
                    pass

            if _e_parede_antibot(pagina.url, pagina.content()):
                html, print_tela = capturar_estado(pagina)
                return captcha_falhou(
                    _mensagem_antibot(headless),
                    salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
                )

            # Desafio vencido: guarda a sessão para as próximas emissões.
            _guardar_sessao(pagina, sessao)

        # ------------------------------------------------------ preenchimento
        campo = _achar_campo_cnpj(pagina)
        if campo is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Não encontrei o campo do CNPJ na página do CRF — o site da Caixa "
                "provavelmente mudou de layout. Os arquivos de depuração estão em Logs.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        # CNPJ completo, só números, e a UF fica em branco de propósito:
        # a UF só se aplica à consulta pelo CNPJ básico de 8 dígitos.
        campo.fill(numero)

        botao = _achar_botao(pagina, PISTAS_BOTAO_CONSULTAR)
        if botao is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Não encontrei o botão de consultar na página do CRF.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )
        botao.click()
        pagina.wait_for_load_state("networkidle")

        # O desafio pode aparecer só depois do envio.
        if _e_parede_antibot(pagina.url, pagina.content()):
            html, print_tela = capturar_estado(pagina)
            return captcha_falhou(
                _mensagem_antibot(headless),
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        texto_pagina = pagina.inner_text("body")
        situacao = _situacao_do_texto(texto_pagina)
        logger.info("[FGTS] %s: situação lida = %s", formatar_cnpj(numero), situacao)

        # ------------------------------------------------------- irregular
        if situacao == "IRREGULAR":
            html, print_tela = capturar_estado(pagina)
            debug = salvar_debug(
                pasta_debug, TIPO, numero, html=html, screenshot=print_tela
            )
            resultado = ResultadoConsulta(
                status=STATUS_POSITIVA,
                mensagem_erro=(
                    "A Caixa informou situação IRREGULAR: a empresa está com pendência "
                    "no FGTS e por isso o certificado não é emitido. Regularize e "
                    "consulte de novo."
                ),
                html_debug=debug,
            )
            logger.warning("[FGTS] %s está IRREGULAR no FGTS.", formatar_cnpj(numero))
            return resultado

        if situacao is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "A página do CRF não disse nem 'Regular' nem 'Irregular'. "
                "Provavelmente o CNPJ não foi encontrado ou o site mudou.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        # --------------------------------------------------------- regular
        conteudo = _baixar_certificado(pagina, contexto)
        if not parece_pdf(conteudo):
            html, print_tela = capturar_estado(pagina)
            return ResultadoConsulta(
                status=STATUS_NEGATIVA,
                mensagem_erro=(
                    "A Caixa informou situação REGULAR, mas não consegui baixar o PDF do "
                    "certificado. Emita o CRF à mão no site e anexe pela tela de Histórico."
                ),
                html_debug=salvar_debug(
                    pasta_debug, TIPO, numero, html=html, screenshot=print_tela
                ),
            )

        destino = salvar_pdf(conteudo, contexto["pasta_certidoes"], numero, TIPO)
        texto_pdf = texto_do_pdf(destino)
        emissao, validade = validade_com_fallback(destino, regras, date.today())

        _guardar_sessao(pagina, sessao)

        logger.info(
            "[FGTS] %s: REGULAR, certificado salvo (validade %s)",
            formatar_cnpj(numero),
            validade,
        )
        return ResultadoConsulta(
            status=STATUS_NEGATIVA,
            caminho_pdf=str(destino),
            data_emissao=emissao,
            data_validade=validade,
            numero_certidao=_numero_do_crf(texto_pdf) or _numero_do_crf(texto_pagina),
        )

    except Exception as e:
        logger.exception("Erro inesperado no robô do FGTS")
        html = print_tela = None
        if pagina is not None:
            html, print_tela = capturar_estado(pagina)
        return erro(
            f"Erro inesperado no robô do FGTS: {e}",
            salvar_debug(
                pasta_debug, TIPO, numero, html=html, screenshot=print_tela, extra=str(e)
            ),
        )
    finally:
        fechar_navegador(pw, navegador)


def _guardar_sessao(pagina, arquivo: Path | None) -> None:
    """Salva os cookies para não precisar resolver o desafio toda vez."""
    if not arquivo:
        return
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        pagina.context.storage_state(path=str(arquivo))
        logger.debug("Sessão do FGTS guardada em %s", arquivo)
    except Exception as e:
        logger.debug("Não consegui guardar a sessão do FGTS: %s", e)


def _baixar_certificado(pagina, contexto: dict) -> bytes | None:
    """Obtém o PDF do certificado depois de a situação vir como Regular.

    A Caixa abre o certificado de dois jeitos, dependendo da versão do site:
    como download de arquivo ou como uma nova aba para impressão. Os dois são
    tratados aqui.
    """
    botao = _achar_botao(pagina, PISTAS_BOTAO_IMPRIMIR)
    if botao is None:
        logger.info("[FGTS] Não achei botão de impressão; tentando gerar PDF da página.")
        return _pdf_da_pagina(pagina)

    # 1) Download de arquivo.
    try:
        with pagina.expect_download(timeout=25000) as espera:
            botao.click()
        caminho = espera.value.path()
        if caminho:
            return Path(caminho).read_bytes()
    except Exception:
        pass

    # 2) Nova aba com o certificado.
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

    # 3) O certificado abriu na própria página.
    return _pdf_da_pagina(pagina)


def _pdf_da_pagina(pagina) -> bytes | None:
    """Transforma a página do certificado em PDF.

    Só funciona com o navegador invisível (limitação do Chromium). Com o
    navegador visível, o usuário salva pelo próprio Ctrl+P e anexa no Histórico.
    """
    try:
        return pagina.pdf(format="A4", print_background=True)
    except Exception as e:
        logger.info("Não consegui gerar o PDF da página do CRF: %s", e)
        return None
