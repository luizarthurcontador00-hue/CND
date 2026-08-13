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

4. AS ROTAS FICAM DEPOIS DO "#", e isso importa: sem o "#" a página abre em
   branco. O endereço correto da tela de CNPJ é

     https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj

   Rotas do portal, lidas do próprio pacote JavaScript dele:
     #/home/cnpj  #/home/cpf  #/home/cib  #/home/cno
     #/emitir     #/consultar (2ª via)    #/resultado
   O campo do documento se chama "niContribuinte" (placeholder "Informe o CNPJ").

   Os dois endereços podem ser trocados no config.yaml (url_emitir e
   url_consultar) sem mexer no código — portais de governo mudam de endereço
   sem avisar, e isso já aconteceu duas vezes com este.

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

#: Endereço da tela de emissão por CNPJ.
#: O portal é uma aplicação Angular com rotas depois do "#" — o "#/home/cnpj"
#: NÃO é enfeite, é o caminho de verdade. Sem ele a página abre em branco.
#: Rotas existentes no portal, lidas do próprio código dele:
#:   #/home/cnpj  #/home/cpf  #/home/cib  #/home/cno
#:   #/emitir     #/consultar (2ª via)    #/resultado
URL_EMITIR_PADRAO = f"{BASE}/#/home/cnpj"
URL_CONSULTAR_PADRAO = f"{BASE}/#/consultar"

# Mantidos para compatibilidade e para os testes.
URL_EMITIR = URL_EMITIR_PADRAO
URL_CONSULTAR = URL_CONSULTAR_PADRAO


def _enderecos(regras: dict) -> tuple[str, str]:
    """Endereços do portal, com o config.yaml tendo a última palavra.

    Portais de governo trocam de endereço sem aviso. Deixar isso no
    config.yaml permite consertar colando o novo endereço, sem mexer no código
    nem esperar por uma versão nova do sistema.
    """
    return (
        str(regras.get("url_emitir") or URL_EMITIR_PADRAO).strip(),
        str(regras.get("url_consultar") or URL_CONSULTAR_PADRAO).strip(),
    )

#: Onde procurar o campo do documento, em ordem de preferência.
#:
#: ATENÇÃO — o portal usa o Design System do gov.br, e nele o
#: formControlName fica no COMPONENTE QUE EMBRULHA o campo
#: (<br-input formcontrolname="niContribuinte"><input ...></br-input>),
#: não no <input> em si. Escrever no embrulho dá o erro
#: "Element is not an <input>". Por isso a busca procura sempre o <input>
#: de verdade, inclusive dentro do embrulho.
SELETORES_DOCUMENTO = (
    'input[formcontrolname="niContribuinte"]',
    '[formcontrolname="niContribuinte"] input',
    'input[name="niContribuinte"]',
    '[formcontrolname="niContribuinte"] textarea',
    'input[placeholder*="CNPJ" i]',
    'input[id*="niContribuinte" i]',
)

#: Usado só para esperar a tela aparecer.
SELETOR_DOCUMENTO = ", ".join(SELETORES_DOCUMENTO)
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


def _campo_documento(pagina):
    """Devolve o <input> onde se digita o CNPJ, ou None.

    Procura nesta ordem: o próprio input com o formControlName, o input que
    está DENTRO do componente com o formControlName, e por fim qualquer campo
    de texto visível — o que salva a situação se o portal trocar os nomes.
    """
    for seletor in SELETORES_DOCUMENTO:
        for elemento in pagina.query_selector_all(seletor):
            if _e_campo_editavel(elemento):
                return elemento

    # Último recurso: se só existe um campo de texto visível na tela, é ele.
    visiveis = [
        e
        for e in pagina.query_selector_all("input:not([type=hidden])")
        if _e_campo_editavel(e)
    ]
    if len(visiveis) == 1:
        logger.info("[FEDERAL] Usei o único campo de texto visível da tela.")
        return visiveis[0]
    return None


def _e_campo_editavel(elemento) -> bool:
    """True só para <input>/<textarea> visível em que dá para digitar."""
    if elemento is None:
        return False
    try:
        if not elemento.is_visible() or not elemento.is_enabled():
            return False
        etiqueta = (elemento.evaluate("e => e.tagName") or "").upper()
        if etiqueta not in ("INPUT", "TEXTAREA"):
            return False
        tipo = (elemento.get_attribute("type") or "text").lower()
        return tipo in ("text", "tel", "number", "search", "")
    except Exception:
        return False


def _esperar_formulario(pagina, segundos: int = 30) -> bool:
    """Espera o Angular desenhar o campo do documento.

    Em aplicação Angular o HTML inicial vem praticamente vazio: os campos só
    aparecem quando o JavaScript termina de montar a tela. Procurar o campo
    antes disso encontra nada e faz o robô achar que o site mudou.
    """
    try:
        pagina.wait_for_selector(SELETOR_DOCUMENTO, timeout=segundos * 1000)
        return True
    except Exception:
        logger.info("[FEDERAL] O campo do documento não apareceu em %ds.", segundos)
        return False


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


def _hcaptcha_resolvido(pagina) -> bool:
    """True quando o campo escondido do hCaptcha já tem resposta."""
    try:
        return bool(
            pagina.evaluate(
                """() => {
                    const c = document.querySelector('[name="h-captcha-response"]')
                          || document.querySelector('[name="g-recaptcha-response"]');
                    return c && c.value && c.value.length > 20;
                }"""
            )
        )
    except Exception:
        return False


def _esperar_usuario_resolver(pagina, segundos: int) -> bool:
    """Deixa a pessoa clicar no "não sou um robô" na janela do navegador.

    É o mesmo recurso que faz o FGTS funcionar sem serviço pago: em vez de
    desistir, o robô abre a tela, preenche o que sabe preencher e espera a
    pessoa resolver só o desafio. Só faz sentido com o navegador visível
    (config.yaml -> navegador.headless: false).
    """
    logger.info(
        "[FEDERAL] Resolva o \"não sou um robô\" na janela do navegador. "
        "Aguardando até %d segundos.",
        segundos,
    )
    try:
        pagina.wait_for_function(
            """() => {
                const c = document.querySelector('[name="h-captcha-response"]')
                      || document.querySelector('[name="g-recaptcha-response"]');
                return c && c.value && c.value.length > 20;
            }""",
            timeout=segundos * 1000,
        )
        logger.info("[FEDERAL] Desafio resolvido na tela. Seguindo com a emissão.")
        return True
    except Exception:
        logger.info("[FEDERAL] O desafio não foi resolvido dentro do tempo.")
        return False


def _responder_hcaptcha(pagina, contexto: dict) -> bool:
    """Consegue passar pelo hCaptcha? Tenta, nesta ordem:

    1. serviço pago, se estiver configurado no config.yaml;
    2. a própria pessoa, quando o navegador está visível;
    3. desiste — e aí o robô registra CAPTCHA_FALHOU, sem travar nada.
    """
    cfg = contexto.get("config")
    regras = contexto.get("regras") or {}

    if _hcaptcha_resolvido(pagina):
        return True

    if cfg is None or not cfg.captcha_disponivel:
        # Sem serviço pago: se a janela está aberta, a pessoa resolve.
        if not contexto.get("headless", True):
            segundos = int(regras.get("segundos_para_resolver", 180))
            return _esperar_usuario_resolver(pagina, segundos)
        return False

    sitekey = _sitekey_hcaptcha(pagina)
    if not sitekey:
        logger.info("[FEDERAL] Não achei a chave do hCaptcha na página.")
        return False

    logger.info("[FEDERAL] Pedindo a resolução do hCaptcha ao serviço configurado…")
    token = resolver_hcaptcha(sitekey, pagina.url, cfg)
    if not token:
        # O serviço falhou, mas se a janela está aberta a pessoa ainda resolve.
        if not contexto.get("headless", True):
            segundos = int(regras.get("segundos_para_resolver", 180))
            return _esperar_usuario_resolver(pagina, segundos)
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
    if contexto.get("headless", True):
        return (
            "O portal da Receita exige o \"não sou um robô\" (hCaptcha), e o navegador "
            "está em modo invisível, então não há como respondê-lo. O jeito mais simples, "
            "sem custo: abra o config.yaml, mude navegador.headless para false e rode a "
            "consulta da Federal de novo — a janela do navegador vai abrir com o CNPJ já "
            "preenchido e você só clica no quadradinho. O robô continua sozinho a partir "
            "daí. Se preferir, emita à mão e anexe o PDF pela tela de Histórico."
        )
    return (
        "O \"não sou um robô\" do portal da Receita não foi resolvido a tempo na janela "
        "do navegador. Rode de novo e clique no quadradinho quando a janela abrir, ou "
        "emita a certidão à mão e anexe o PDF pela tela de Histórico."
    )


def _guardar_sessao(pagina, arquivo: Path | None) -> None:
    """Guarda os cookies para as próximas emissões começarem adiantadas."""
    if not arquivo:
        return
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        pagina.context.storage_state(path=str(arquivo))
    except Exception as e:
        logger.debug("Não consegui guardar a sessão da Receita: %s", e)


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
    url_emitir, url_consultar = _enderecos(regras)

    pw = navegador = pagina = None
    try:
        pw, navegador, pagina = abrir_navegador(contexto, estado=sessao)
    except NavegadorIndisponivel as e:
        return erro(str(e), permanente=True)
    except Exception as e:
        return erro(f"Não consegui abrir o navegador para a certidão federal: {e}")

    try:
        # ------------------------------------------------- 1) emissão nova
        resultado = _tentar_emitir(
            pagina, numero, contexto, regras, pasta_debug, url_emitir
        )
        if resultado is not None:
            if resultado.sucesso:
                _guardar_sessao(pagina, sessao)
            return resultado

        # ------------------------------------- 2) regra especial da 2ª via
        anterior = contexto.get("emissao_anterior")
        logger.info(
            "[FEDERAL] O portal não emitiu certidão nova. Tentando a 2ª via de uma "
            "certidão anterior ainda válida."
        )
        return _tentar_segunda_via(
            pagina, numero, contexto, regras, pasta_debug, anterior, url_consultar
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

    campo = _campo_documento(pagina)
    if campo is None:
        return False

    try:
        campo.fill(numero)
    except Exception as e:
        # Alguns campos com máscara recusam o preenchimento direto; digitar
        # tecla a tecla funciona neles.
        logger.info("[FEDERAL] Preenchimento direto falhou (%s). Vou digitar.", e)
        try:
            campo.click()
            campo.type(numero, delay=40)
        except Exception as e2:
            logger.warning("[FEDERAL] Não consegui escrever no campo do CNPJ: %s", e2)
            return False

    return True


def _tentar_emitir(pagina, numero, contexto, regras, pasta_debug, url_emitir):
    """Tenta a emissão nova. Devolve ResultadoConsulta, ou None para cair na 2ª via."""
    pagina.goto(url_emitir, wait_until="networkidle")
    # Aplicação Angular: a tela só existe depois que o JavaScript monta a
    # página. Esperar a rede parar não basta.
    _esperar_formulario(pagina)

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


def _tentar_segunda_via(
    pagina, numero, contexto, regras, pasta_debug, anterior, url_consultar
):
    """Recupera a 2ª via de uma certidão anterior ainda válida."""
    pagina.goto(url_consultar, wait_until="networkidle")
    _esperar_formulario(pagina)

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
