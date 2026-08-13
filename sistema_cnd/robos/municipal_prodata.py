"""Certidão Negativa Municipal — sistema Prodata SIG.

Configurado para CALDAS NOVAS (GO):
  https://caldasnovas.prodataweb.inf.br/sig/app.html#/servicosonline/debito-contribuinte

SERVE PARA OUTRAS PREFEITURAS TAMBÉM
------------------------------------
A Prodata atende dezenas de municípios com exatamente o mesmo sistema, mudando
só o endereço. Se você pegar um cliente de outra cidade que use Prodata, basta
trocar `url` em certidoes.MUNICIPAL no config.yaml — o robô continua valendo.

O QUE FOI DESCOBERTO SOBRE O SITE (agosto/2026)
-----------------------------------------------
1. NÃO TEM CAPTCHA. Nenhum hCaptcha, reCAPTCHA ou anti-robô na página. É a
   situação mais fácil das cinco certidões do sistema.

2. É uma aplicação AngularJS, com as telas montadas por JavaScript e rotas
   depois do "#". Por isso o robô abre navegador de verdade.

3. A API interna (rest/servicoContribuinteController/...) EXIGE cabeçalhos de
   autenticação que só o próprio aplicativo sabe gerar — testado, responde
   "Headers de autenticação de requests ausentes". Por isso o robô dirige a
   tela em vez de chamar a API direto.

4. O FLUXO, lido do controlador da própria tela:
     pesquisarContribuintes  -> lista os contribuintes do CNPJ
     (seleciona a linha)     -> o sistema exige um registro selecionado
     imprimirCertidao        -> gera a certidão e devolve o número dela
     relatório .jasper       -> abre o PDF numa tela de visualização

5. REGRA IMPORTANTE: quando há pendência no cadastro, o sistema NÃO emite e
   mostra a mensagem "Foram encontradas pendências em seu cadastro que
   impossibilitam a emissão da certidão". Isso não é falha do robô — é a
   resposta legítima do sistema, e vira status POSITIVA para você agir.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from robos.base import (
    STATUS_NEGATIVA,
    STATUS_POSITIVA,
    STATUS_POSITIVA_COM_EFEITO_NEGATIVA,
    NavegadorIndisponivel,
    ResultadoConsulta,
    abrir_navegador,
    capturar_estado,
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

TIPO = "MUNICIPAL"
NOME_PREFEITURA = "Caldas Novas (GO)"
URL_PADRAO = (
    "https://caldasnovas.prodataweb.inf.br/sig/app.html"
    "#/servicosonline/debito-contribuinte"
)

#: Onde procurar o campo do CNPJ, em ordem de preferência.
#:
#: ATENÇÃO — a tela real usa componentes customizados do Prodata
#: (<pd-input-text>), e o Angular gera para o <input> de dentro um id
#: NUMÉRICO tipo "64inputText". Um id começando com dígito é seletor CSS
#: inválido (document.querySelector('#64inputText') estoura erro), então
#: nunca dá pra contar com o id. O que é estável é o atributo "label" e
#: "tipo" do PRÓPRIO componente <pd-input-text>, escritos pelo template da
#: tela e que não mudam de uma carga de página para outra:
#:   <pd-input-text label="CPF/CNPJ" tipo="cpfCnpj"><input id="64inputText">
SELETORES_CNPJ = (
    'pd-input-text[tipo="cpfCnpj"] input',
    'pd-input-text[label="CPF/CNPJ"] input',
    'pd-input-text[label*="CNPJ" i] input',
    # Alternativas para outras prefeituras Prodata que fujam desse padrão.
    'input[name*="cnpj" i]',
    'input[id*="cnpj" i]',
    'input[placeholder*="cnpj" i]',
)
#: Idem para o botão — pelo texto visível, não por id.
PISTAS_BOTAO_PESQUISAR = ("pesquisar", "consultar", "buscar")
PISTAS_BOTAO_IMPRIMIR = ("imprimir certidao", "imprimir certidão", "imprimir", "certidao", "certidão")

#: O tipo de certidão certo (Débitos/CND) já vem marcado por padrão na tela,
#: mas o robô confirma por segurança — a prefeitura pode mudar o padrão.
#:
#: ATENÇÃO — não é um <input type=radio> nativo. É um componente do Angular
#: Material (<md-radio-button role="radio" aria-checked="true">), sem input
#: nenhum por dentro. Um seletor terminado em " input" nunca acharia nada.
#: ATENÇÃO — "value=2" NÃO é exclusivo desta opção: a tela tem outro grupo
#: de rádio ("Débitos ativos"/"Débitos liquidados por período") que também
#: usa value="1"/"2". Usar só o valor pegaria o radio errado. O aria-label
#: com o texto "CND" é o único jeito confiável de mirar exatamente este.
SELETOR_RADIO_CND = 'md-radio-button[aria-label*="CND" i], [role="radio"][aria-label*="CND" i]'

#: Mensagem que o sistema mostra quando se recusa a emitir.
PISTAS_BLOQUEIO = (
    "pendencias em seu cadastro",
    "impossibilitam a emissao",
    "impossibilitam a emissão",
    "entre em contato com o responsavel",
)
#: Mensagem de quando nada foi encontrado para o CNPJ.
PISTAS_SEM_CADASTRO = (
    "nenhum registro",
    "nao foram encontrados",
    "não foram encontrados",
    "sem resultados",
    "nenhum resultado",
)


# =============================================================================
#  AUXILIARES
# =============================================================================


def _url(regras: dict) -> str:
    """Endereço da prefeitura, configurável para atender outras cidades."""
    return str(regras.get("url") or URL_PADRAO).strip()


def _visivel(elemento) -> bool:
    try:
        return elemento.is_visible() and elemento.is_enabled()
    except Exception:
        return False


def _campo_cnpj(pagina):
    """Acha o campo do CNPJ, sempre devolvendo um <input> de verdade.

    Tenta os seletores estáveis primeiro (atributo do componente). Se nenhum
    bater, usa get_by_label do Playwright, que resolve <label for="..."> pela
    árvore de acessibilidade — funciona mesmo com id numérico, que quebraria
    um seletor CSS "#64inputText".
    """
    for seletor in SELETORES_CNPJ:
        for elemento in pagina.query_selector_all(seletor):
            if _visivel(elemento) and _e_input(elemento):
                return elemento

    try:
        candidato = pagina.get_by_label("CPF/CNPJ", exact=True).element_handle(timeout=3000)
        if candidato and _visivel(candidato) and _e_input(candidato):
            return candidato
    except Exception:
        pass

    return None


def _confirmar_tipo_certidao_cnd(pagina) -> None:
    """Garante que a opção 'Débitos (CND)' está marcada, não a de espólio/outra.

    Sendo um componente Angular Material (não um <input> nativo), a marcação
    fica no atributo aria-checked, e é o próprio elemento que se clica —
    não existe um .check()/.is_checked() de verdade aqui.
    """
    try:
        radio = pagina.query_selector(SELETOR_RADIO_CND)
        if radio and _visivel(radio) and radio.get_attribute("aria-checked") != "true":
            radio.click()
            logger.info("[MUNICIPAL] Marquei a opção 'Débitos (CND)'.")
    except Exception as e:
        logger.debug("Não consegui conferir o tipo de certidão (seguindo com o padrão): %s", e)


def _e_input(elemento) -> bool:
    try:
        return (elemento.evaluate("e => e.tagName") or "").upper() == "INPUT"
    except Exception:
        return False


def _achar_botao(pagina, pistas):
    """Botão cujo texto bate com alguma das pistas, o mais específico primeiro."""
    candidatos = []
    for elemento in pagina.query_selector_all("button, a, input[type=button], input[type=submit]"):
        if not _visivel(elemento):
            continue
        try:
            rotulo = normalizar(
                (elemento.inner_text() or "")
                + " "
                + (elemento.get_attribute("value") or "")
                + " "
                + (elemento.get_attribute("title") or "")
                + " "
                + (elemento.get_attribute("aria-label") or "")
            )
        except Exception:
            continue
        for posicao, pista in enumerate(pistas):
            if pista in rotulo:
                candidatos.append((posicao, elemento))
                break
    candidatos.sort(key=lambda c: c[0])
    return candidatos[0][1] if candidatos else None


def _fechar_popup_se_houver(pagina) -> None:
    """Selecionar o contribuinte na lista abre um popup de detalhes ("Débitos")
    por cima da tela — visto numa emissão real que travou. O popup do
    Bootstrap/AngularJS cobre a TELA INTEIRA (não só a caixa branca visível),
    então o botão "Imprimir certidão" de baixo, mesmo continuando "visível"
    tecnicamente, fica impossível de clicar enquanto ele não for fechado —
    o clique trava esperando o popup sair da frente (60s de timeout).

    O botão de fechar é o "X" no canto do popup (ng-click="fechar()",
    aria-label="Fechar popup" no ícone de dentro).
    """
    try:
        fechar = pagina.query_selector(
            '[role="dialog"] [aria-label="Fechar popup" i], '
            '[role="dialog"] [aria-label*="fechar" i], '
            '[role="dialog"] button.close'
        )
        if fechar and _visivel(fechar):
            fechar.click()
            pagina.wait_for_selector('[role="dialog"]', state="hidden", timeout=5000)
            logger.info("[MUNICIPAL] Fechei o popup de detalhes que abriu ao selecionar o contribuinte.")
    except Exception as e:
        logger.debug("Não havia popup para fechar (ou não consegui fechar): %s", e)


def _selecionar_primeira_linha(pagina) -> bool:
    """O sistema exige um contribuinte selecionado antes de imprimir.

    A grade é o angular-ui-grid (classes .ui-grid-row / .ui-grid-cell,
    confirmadas na página real da prefeitura) dentro de um .ui-grid-canvas —
    daí a preferência pelo seletor mais específico primeiro, para não cair
    sem querer numa célula do cabeçalho da tabela.
    """
    for seletor in (
        ".ui-grid-canvas .ui-grid-row .ui-grid-cell",
        ".ui-grid-row:not(.ui-grid-header) .ui-grid-cell",
        "table tbody tr td",
        "tr[ng-repeat] td",
        ".grid-row td",
    ):
        celulas = [c for c in pagina.query_selector_all(seletor) if _visivel(c)]
        if celulas:
            try:
                celulas[0].click()
                pagina.wait_for_timeout(800)
                logger.info("[MUNICIPAL] Contribuinte selecionado na lista.")
                return True
            except Exception:
                continue
    return False


def _classificar(texto_pdf: str) -> str:
    t = normalizar(texto_pdf)
    if "positiva com efeito" in t or "efeito de negativa" in t or "efeitos de negativa" in t:
        return STATUS_POSITIVA_COM_EFEITO_NEGATIVA
    if "negativa" in t and "positiva" not in t:
        return STATUS_NEGATIVA
    if "positiva" in t:
        return STATUS_POSITIVA
    if "nada consta" in t or "nao consta debito" in t or "nao constam debitos" in t:
        return STATUS_NEGATIVA
    logger.warning("Não reconheci o tipo da certidão municipal; assumindo negativa.")
    return STATUS_NEGATIVA


def _numero_da_certidao(texto: str) -> str | None:
    for padrao in (
        r"n[º°.]?\s*(?:da\s+)?certid[ãa]o\s*:?\s*([\w./-]{4,30})",
        r"certid[ãa]o\s*n[º°.:]*\s*([\w./-]{4,30})",
    ):
        achado = re.search(padrao, texto or "", re.I)
        if achado:
            return achado.group(1).strip(" .")
    return None


# =============================================================================
#  ROBÔ
# =============================================================================


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão negativa municipal do CNPJ informado."""
    numero = limpar_cnpj(cnpj)
    if len(numero) != 14:
        return erro(f"CNPJ inválido para a certidão municipal: {cnpj!r}", permanente=True)

    pasta_debug = contexto.get("pasta_debug")
    regras = contexto.get("regras") or {}
    endereco = _url(regras)

    pw = navegador = pagina = None
    try:
        pw, navegador, pagina = abrir_navegador(contexto)
    except NavegadorIndisponivel as e:
        return erro(str(e), permanente=True)
    except Exception as e:
        return erro(f"Não consegui abrir o navegador para a certidão municipal: {e}")

    # Guarda qualquer PDF que o site devolver, seja em aba nova, download ou
    # visualizador — os três acabam passando por aqui.
    pdfs: list[bytes] = []

    def _guardar_pdf(resposta):
        try:
            tipo = (resposta.headers or {}).get("content-type", "")
            if "pdf" in tipo.lower():
                corpo = resposta.body()
                if parece_pdf(corpo):
                    pdfs.append(corpo)
                    logger.info("[MUNICIPAL] PDF capturado (%d KB).", len(corpo) // 1024)
        except Exception:
            pass

    try:
        pagina.context.on("response", _guardar_pdf)
        pagina.goto(endereco, wait_until="networkidle")

        # ------------------------------------------------- 1) campo do CNPJ
        campo = None
        for _ in range(3):
            campo = _campo_cnpj(pagina)
            if campo:
                break
            pagina.wait_for_timeout(3000)

        if campo is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Não encontrei o campo do CNPJ na tela da prefeitura. O site pode ter "
                "mudado, ou o endereço em certidoes.MUNICIPAL.url pode estar errado.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        _confirmar_tipo_certidao_cnd(pagina)
        campo.fill(formatar_cnpj(numero))

        # ------------------------------------------------------ 2) pesquisar
        botao = _achar_botao(pagina, PISTAS_BOTAO_PESQUISAR)
        if botao is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Não encontrei o botão de pesquisar na tela da prefeitura.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )
        botao.click()
        pagina.wait_for_load_state("networkidle")
        pagina.wait_for_timeout(1500)

        texto = pagina.inner_text("body")
        if any(p in normalizar(texto) for p in PISTAS_SEM_CADASTRO):
            html, print_tela = capturar_estado(pagina)
            return erro(
                f"A prefeitura não encontrou cadastro para o CNPJ "
                f"{formatar_cnpj(numero)}. Confira se a empresa realmente tem "
                "inscrição neste município.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
                permanente=True,
            )

        # ------------------------------------ 3) selecionar o contribuinte
        # O sistema recusa a impressão sem um registro selecionado na lista.
        _selecionar_primeira_linha(pagina)
        # Selecionar a linha abre um popup de detalhes por cima da tela —
        # fecha antes de seguir, senão o clique em "Imprimir certidão" trava.
        _fechar_popup_se_houver(pagina)

        # -------------------------------------------------- 4) imprimir
        imprimir = _achar_botao(pagina, PISTAS_BOTAO_IMPRIMIR)
        if imprimir is None:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Não encontrei o botão de imprimir a certidão.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        imprimir.click()
        # A certidão abre numa tela de visualização; dá tempo de ela carregar.
        for _ in range(20):
            if pdfs:
                break
            pagina.wait_for_timeout(1500)

        texto = pagina.inner_text("body")
        t = normalizar(texto)

        # O sistema se recusa a emitir quando há pendência no cadastro.
        if not pdfs and any(p in t for p in PISTAS_BLOQUEIO):
            html, print_tela = capturar_estado(pagina)
            logger.warning("[MUNICIPAL] %s tem pendência no cadastro.", formatar_cnpj(numero))
            return ResultadoConsulta(
                status=STATUS_POSITIVA,
                mensagem_erro=(
                    "A prefeitura informou que há pendências no cadastro que impedem a "
                    "emissão da certidão. É preciso resolver isso junto ao município — "
                    "não é falha do robô."
                ),
                html_debug=salvar_debug(
                    pasta_debug, TIPO, numero, html=html, screenshot=print_tela
                ),
            )

        # A certidão pode ter aberto em aba nova.
        if not pdfs:
            for outra in pagina.context.pages:
                if outra is pagina:
                    continue
                try:
                    outra.wait_for_load_state("networkidle")
                    conteudo = outra.pdf(format="A4", print_background=True)
                    if parece_pdf(conteudo):
                        pdfs.append(conteudo)
                        break
                except Exception:
                    continue

        if not pdfs:
            html, print_tela = capturar_estado(pagina)
            return erro(
                "Cheguei até a impressão, mas a certidão não chegou como PDF. "
                "O print e o HTML da tela estão em Logs.",
                salvar_debug(pasta_debug, TIPO, numero, html=html, screenshot=print_tela),
            )

        # ----------------------------------------------------- 5) guardar
        destino = salvar_pdf(pdfs[-1], contexto["pasta_certidoes"], numero, TIPO)
        texto_pdf = texto_do_pdf(destino)
        status = _classificar(texto_pdf)
        emissao, validade = validade_com_fallback(destino, regras, date.today())

        logger.info(
            "[MUNICIPAL] %s: %s (validade %s)", formatar_cnpj(numero), status, validade
        )
        return ResultadoConsulta(
            status=status,
            caminho_pdf=str(destino),
            data_emissao=emissao,
            data_validade=validade,
            numero_certidao=_numero_da_certidao(texto_pdf),
        )

    except Exception as e:
        logger.exception("Erro inesperado no robô da certidão municipal")
        html = print_tela = None
        if pagina is not None:
            html, print_tela = capturar_estado(pagina)
        return erro(
            f"Erro inesperado no robô da certidão municipal: {e}",
            salvar_debug(
                pasta_debug, TIPO, numero, html=html, screenshot=print_tela, extra=str(e)
            ),
        )
    finally:
        fechar_navegador(pw, navegador)
