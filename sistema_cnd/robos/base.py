"""Base comum a todos os robôs: o contrato e as ferramentas compartilhadas.

CONTRATO — todo módulo em robos/ precisa expor:

    def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta

Nada mais. Quem chama (o serviço de emissão) não sabe nem se o robô usa
navegador, requisição direta ou mágica — só recebe o ResultadoConsulta.

O que vem dentro de `contexto`:
    empresa            dict com razao_social, uf, municipio, inscricao_estadual,
                       inscricao_municipal, regime_tributario, e_matriz, cnpj_matriz
    config             objeto de configuração completo (app.config.config)
    regras             dict com as regras do config.yaml para ESTA certidão
    pasta_certidoes    Path da pasta raiz onde salvar os PDFs
    pasta_debug        Path da pasta onde salvar prints/HTML quando der erro
    headless           bool — navegador invisível ou visível
    tipo_certidao      str — "CNDT", "FGTS", ...
    emissao_anterior   dict|None — última emissão bem-sucedida desta certidão
                       (usado pela Federal para buscar a 2ª via)
"""

from __future__ import annotations

import logging
import random
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
#  RESULTADO — o que todo robô devolve
# =============================================================================

# Os status possíveis. Repetidos aqui como texto simples de propósito: assim o
# pacote robos/ não depende de app/, e um robô pode ser testado sozinho.
STATUS_NEGATIVA = "NEGATIVA"
STATUS_POSITIVA_COM_EFEITO_NEGATIVA = "POSITIVA_COM_EFEITO_NEGATIVA"
STATUS_POSITIVA = "POSITIVA"
STATUS_ERRO_SITE = "ERRO_SITE"
STATUS_CAPTCHA_FALHOU = "CAPTCHA_FALHOU"
STATUS_SEM_PENDENCIA_MAS_NAO_EMITIU = "SEM_PENDENCIA_MAS_NAO_EMITIU"


@dataclass
class ResultadoConsulta:
    """Resultado padronizado de uma consulta a um site de certidão."""

    status: str
    caminho_pdf: str | None = None
    data_emissao: date | None = None
    data_validade: date | None = None
    numero_certidao: str | None = None
    mensagem_erro: str | None = None
    #: Caminho do print de tela + HTML salvos quando dá erro. É isso que
    #: permite descobrir o que mudou no site.
    html_debug: str | None = None
    #: True quando repetir não adianta: certidão desligada, robô não
    #: implementado, CNPJ inexistente na base do órgão, captcha sem serviço
    #: configurado. Evita gastar minutos repetindo o que nunca vai dar certo.
    #: Erros de rede/instabilidade do site devem deixar isso em False.
    permanente: bool = False
    #: Espaço livre para o robô guardar informação extra (não vai para o banco).
    detalhes: dict[str, Any] = field(default_factory=dict)

    @property
    def sucesso(self) -> bool:
        """True quando um documento foi realmente obtido."""
        return self.status in (
            STATUS_NEGATIVA,
            STATUS_POSITIVA_COM_EFEITO_NEGATIVA,
            STATUS_POSITIVA,
        )

    def como_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        if self.sucesso:
            return f"{self.status} (validade {self.data_validade or 'não lida'})"
        return f"{self.status}: {self.mensagem_erro or 'sem detalhes'}"


# Atalhos para os robôs devolverem erro sem repetir código.


def erro(
    mensagem: str, html_debug: str | None = None, permanente: bool = False
) -> ResultadoConsulta:
    return ResultadoConsulta(
        status=STATUS_ERRO_SITE,
        mensagem_erro=mensagem,
        html_debug=html_debug,
        permanente=permanente,
    )


def captcha_falhou(
    mensagem: str, html_debug: str | None = None, permanente: bool = False
) -> ResultadoConsulta:
    return ResultadoConsulta(
        status=STATUS_CAPTCHA_FALHOU,
        mensagem_erro=mensagem,
        html_debug=html_debug,
        permanente=permanente,
    )


def sem_pendencia_nao_emitiu(mensagem: str, html_debug: str | None = None) -> ResultadoConsulta:
    return ResultadoConsulta(
        status=STATUS_SEM_PENDENCIA_MAS_NAO_EMITIU,
        mensagem_erro=mensagem,
        html_debug=html_debug,
    )


class RoboNaoImplementado(Exception):
    """Robô ainda sem implementação (esqueleto aguardando definição)."""


# =============================================================================
#  CNPJ
# =============================================================================


def limpar_cnpj(cnpj: str) -> str:
    """Deixa só os números. '12.345.678/0001-90' -> '12345678000190'."""
    return re.sub(r"\D", "", cnpj or "")


def formatar_cnpj(cnpj: str) -> str:
    c = limpar_cnpj(cnpj)
    if len(c) != 14:
        return cnpj
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def _digito_cnpj(base: str) -> int:
    """Calcula um dígito verificador de CNPJ a partir dos dígitos anteriores."""
    tamanho = len(base)
    pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def cnpj_valido(cnpj: str) -> bool:
    """Confere os dois dígitos verificadores."""
    c = limpar_cnpj(cnpj)
    if len(c) != 14 or c == c[0] * 14:
        return False
    return int(c[12]) == _digito_cnpj(c[:12]) and int(c[13]) == _digito_cnpj(c[:13])


def cnpj_da_matriz(cnpj: str) -> str:
    """CNPJ da matriz do mesmo grupo: mesma raiz, filial 0001.

    ATENÇÃO — os dois últimos dígitos são verificadores e dependem dos doze
    anteriores. Trocar só o número da filial (0002 -> 0001) e manter os dígitos
    antigos produz um CNPJ INVÁLIDO, que os sites do governo recusam. Por isso
    os verificadores são recalculados aqui.
    """
    c = limpar_cnpj(cnpj)
    if len(c) != 14:
        return c
    base = c[:8] + "0001"
    return base + str(_digito_cnpj(base)) + str(_digito_cnpj(base + str(_digito_cnpj(base))))


# =============================================================================
#  DATAS
# =============================================================================

_FORMATOS_DATA = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")


def converter_data(texto: str | None) -> date | None:
    """Converte '12/08/2026' (e variações) em objeto date. None se não der."""
    if not texto:
        return None
    limpo = str(texto).strip()
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(limpo, formato).date()
        except ValueError:
            continue
    return None


def procurar_datas(texto: str) -> list[date]:
    """Todas as datas dd/mm/aaaa encontradas em um texto, na ordem."""
    achadas = []
    for bruto in re.findall(r"\b\d{2}[/.-]\d{2}[/.-]\d{2,4}\b", texto or ""):
        convertida = converter_data(bruto.replace(".", "/").replace("-", "/"))
        if convertida:
            achadas.append(convertida)
    return achadas


#: Meses por extenso, já sem acento (o texto é normalizado antes da busca).
MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

_DATA_POR_EXTENSO = re.compile(
    r"\b(\d{1,2})\s*(?:de\s+)?(" + "|".join(MESES) + r")\s*(?:de\s+)?(\d{4})\b"
)


def procurar_datas_por_extenso(texto: str) -> list[date]:
    """Datas escritas por extenso, como '13 AGOSTO DE 2026' ou '1 de maio de 2026'.

    A SEFAZ-GO imprime a data de emissão só neste formato — não há nenhum
    dd/mm/aaaa no PDF dela.
    """
    achadas = []
    for dia, mes, ano in _DATA_POR_EXTENSO.findall(normalizar(texto)):
        try:
            achadas.append(date(int(ano), MESES[mes], int(dia)))
        except ValueError:
            continue
    return achadas


def todas_as_datas(texto: str) -> list[date]:
    """Datas em número e por extenso, juntas."""
    return procurar_datas(texto) + procurar_datas_por_extenso(texto)


_PRAZO_EM_DIAS = re.compile(r"valid[ao]\s+por\s+(\d{1,4})\s*dias?")


def prazo_em_dias(texto: str) -> int | None:
    """Lê 'Certidao VALIDA POR 120 DIAS' e devolve 120.

    Alguns órgãos não imprimem a data de vencimento, só o prazo. Nesse caso a
    validade é calculada a partir da data de emissão do próprio documento —
    o que continua sendo melhor do que usar o prazo fixo do config.yaml.
    """
    achado = _PRAZO_EM_DIAS.search(normalizar(texto))
    return int(achado.group(1)) if achado else None


# =============================================================================
#  ARQUIVOS
# =============================================================================


def caminho_pdf(
    pasta_raiz: Path | str,
    cnpj: str,
    tipo_certidao: str,
    quando: date | None = None,
) -> Path:
    """Monta certidoes/{CNPJ}/{AAAA-MM}/{TIPO}_{AAAAMMDD}.pdf

    Se já existir arquivo com esse nome (duas emissões no mesmo dia), o sistema
    acrescenta _2, _3... em vez de sobrescrever — nunca perdemos um documento.
    """
    quando = quando or date.today()
    pasta = Path(pasta_raiz) / limpar_cnpj(cnpj) / quando.strftime("%Y-%m")
    pasta.mkdir(parents=True, exist_ok=True)

    base = f"{tipo_certidao}_{quando.strftime('%Y%m%d')}"
    destino = pasta / f"{base}.pdf"
    contador = 2
    while destino.exists():
        destino = pasta / f"{base}_{contador}.pdf"
        contador += 1
    return destino


def salvar_pdf(
    conteudo: bytes,
    pasta_raiz: Path | str,
    cnpj: str,
    tipo_certidao: str,
    quando: date | None = None,
) -> Path:
    destino = caminho_pdf(pasta_raiz, cnpj, tipo_certidao, quando)
    destino.write_bytes(conteudo)
    logger.info("PDF salvo em %s (%d KB)", destino, len(conteudo) // 1024)
    return destino


def parece_pdf(conteudo: bytes) -> bool:
    """Confere se o download é mesmo um PDF e não uma página de erro."""
    return bool(conteudo) and conteudo[:5] == b"%PDF-" and len(conteudo) > 1000


def salvar_debug(
    pasta_debug: Path | str,
    tipo_certidao: str,
    cnpj: str,
    html: str | None = None,
    screenshot: bytes | None = None,
    extra: str | None = None,
) -> str | None:
    """Salva print de tela + HTML da página quando algo dá errado.

    Devolve o caminho base (sem extensão) para gravar no campo html_debug.
    É este par de arquivos que permite descobrir o que mudou no site.
    """
    if html is None and screenshot is None and extra is None:
        return None

    pasta = Path(pasta_debug)
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = pasta / f"{carimbo}_{tipo_certidao}_{limpar_cnpj(cnpj)}"

    try:
        if html is not None:
            base.with_suffix(".html").write_text(html, encoding="utf-8", errors="replace")
        if screenshot is not None:
            base.with_suffix(".png").write_bytes(screenshot)
        if extra is not None:
            base.with_suffix(".txt").write_text(extra, encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("Não consegui salvar os arquivos de depuração: %s", e)
        return None

    logger.info("Arquivos de depuração salvos em %s.*", base)
    return str(base)


# =============================================================================
#  LEITURA DO PDF (a fonte da verdade sobre a validade)
# =============================================================================


def texto_do_pdf(caminho: Path | str) -> str:
    """Extrai o texto de um PDF. Devolve "" se não conseguir ler."""
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        logger.warning("pdfplumber não instalado — não dá para ler a validade do PDF.")
        return ""

    try:
        partes = []
        with pdfplumber.open(str(caminho)) as pdf:
            for pagina in pdf.pages:
                partes.append(pagina.extract_text() or "")
        return "\n".join(partes)
    except Exception as e:
        logger.warning("Não consegui ler o texto do PDF %s: %s", caminho, e)
        return ""


def normalizar(texto: str) -> str:
    """Minúsculas e sem acento — para comparar texto de site sem sofrimento."""
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


#: Trechos que costumam anteceder a data de validade nos PDFs de certidão.
_PISTAS_VALIDADE = (
    "valida ate",
    "validade ate",
    "valido ate",
    "validade:",
    "valida ate o dia",
    "vencimento",
    "data de validade",
    "sera valida ate",
)

_PISTAS_EMISSAO = (
    "emitida as",
    "data de emissao",
    "emissao:",
    "emitido em",
    "expedida em",
    "local e data",  # SEFAZ-GO: "LOCAL E DATA: GOIANIA, 13 AGOSTO DE 2026"
)


def extrair_datas_do_pdf(caminho: Path | str) -> tuple[date | None, date | None]:
    """Lê (data_emissao, data_validade) de dentro do PDF.

    Regra geral do sistema: a validade vem SEMPRE do PDF quando possível.
    Prazo fixo do config.yaml é só plano B — os prazos legais mudam.
    """
    texto = texto_do_pdf(caminho)
    if not texto:
        return None, None
    return extrair_datas_do_texto(texto)


def extrair_datas_do_texto(texto: str) -> tuple[date | None, date | None]:
    """Mesma lógica de extrair_datas_do_pdf, mas a partir de um texto pronto.

    Entende três jeitos de o documento informar a validade, nesta ordem:
      1. uma data explícita depois de "válida até" / "vencimento";
      2. um prazo em dias ("VÁLIDA POR 120 DIAS") somado à data de emissão —
         é assim que a SEFAZ-GO faz, e ela nem imprime data em número;
      3. plano B: a data futura mais distante encontrada no documento.
    """
    limpo = normalizar(texto)
    emissao: date | None = None
    validade: date | None = None

    def _primeira_data_depois(pista: str) -> date | None:
        pos = limpo.find(pista)
        if pos < 0:
            return None
        # Olha os 120 caracteres seguintes à pista.
        trecho = limpo[pos : pos + 120]
        datas = todas_as_datas(trecho)
        return datas[0] if datas else None

    for pista in _PISTAS_VALIDADE:
        validade = _primeira_data_depois(pista)
        if validade:
            break

    for pista in _PISTAS_EMISSAO:
        emissao = _primeira_data_depois(pista)
        if emissao:
            break

    todas = todas_as_datas(limpo)

    if emissao is None and todas:
        # A emissão é a data passada mais recente do documento — as outras
        # costumam ser referências legais antigas.
        passadas = [d for d in todas if d <= date.today()]
        if passadas:
            emissao = max(passadas)

    # Prazo em dias, quando não há data de vencimento impressa.
    if validade is None:
        dias = prazo_em_dias(limpo)
        if dias and emissao:
            validade = emissao + timedelta(days=dias)

    # Plano B: a data futura mais distante do documento.
    if validade is None:
        futuras = [d for d in todas if d >= date.today()]
        if futuras:
            validade = max(futuras)

    return emissao, validade


def validade_com_fallback(
    caminho_do_pdf: Path | str | None,
    regras: dict,
    data_emissao: date | None = None,
) -> tuple[date | None, date | None]:
    """(emissao, validade) lendo do PDF; se falhar, usa o prazo do config.yaml."""
    emissao_lida = validade_lida = None
    if caminho_do_pdf:
        emissao_lida, validade_lida = extrair_datas_do_pdf(caminho_do_pdf)

    emissao_final = emissao_lida or data_emissao or date.today()

    if validade_lida:
        return emissao_final, validade_lida

    dias = int(regras.get("validade_padrao_dias") or 0)
    if dias > 0:
        logger.info(
            "Não consegui ler a validade no PDF; usando o prazo padrão de %d dias.", dias
        )
        return emissao_final, emissao_final + timedelta(days=dias)

    return emissao_final, None


# =============================================================================
#  EDUCAÇÃO COM OS SITES
# =============================================================================


def pausa_educada(config_execucao: dict | None = None) -> float:
    """Espera um tempo aleatório entre requisições ao mesmo site.

    O intervalo variável evita o padrão robótico que dispara bloqueio de IP.
    """
    config_execucao = config_execucao or {}
    minimo = float(config_execucao.get("intervalo_minimo_segundos", 3))
    maximo = float(config_execucao.get("intervalo_maximo_segundos", 8))
    if maximo < minimo:
        minimo, maximo = maximo, minimo
    espera = random.uniform(minimo, maximo)
    logger.debug("Aguardando %.1fs antes da próxima consulta.", espera)
    time.sleep(espera)
    return espera


# =============================================================================
#  NAVEGADOR (Playwright) — usado pelos robôs que precisam de navegador
# =============================================================================


class NavegadorIndisponivel(Exception):
    """Playwright não instalado ou navegador não baixado."""


def abrir_navegador(contexto: dict, estado: Path | str | None = None):
    """Devolve (playwright, browser, page) já configurados pelo config.yaml.

    `estado` é o arquivo de sessão (cookies) gravado por uma execução anterior.
    Serve para não repetir a verificação anti-robô da Caixa a cada emissão:
    resolve-se uma vez e as próximas aproveitam a sessão já liberada.

    Uso dentro de um robô:

        pw = navegador = pagina = None
        try:
            pw, navegador, pagina = abrir_navegador(contexto)
            ...
        finally:
            fechar_navegador(pw, navegador)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise NavegadorIndisponivel(
            "Playwright não está instalado. Rode: pip install -r requirements.txt"
        ) from e

    cfg = contexto.get("config")
    opcoes = cfg.navegador if cfg else {}
    headless = contexto.get("headless", opcoes.get("headless", True))
    timeout_ms = int(opcoes.get("timeout_ms", 60000))
    devagar = int(opcoes.get("camera_lenta_ms", 0))

    pw = sync_playwright().start()
    try:
        navegador = pw.chromium.launch(headless=headless, slow_mo=devagar or 0)
    except Exception as e:  # pragma: no cover
        pw.stop()
        raise NavegadorIndisponivel(
            "Não consegui abrir o navegador. Rode uma vez: python -m playwright install chromium"
        ) from e

    argumentos_contexto = {
        "user_agent": opcoes.get("user_agent"),
        "accept_downloads": True,
        "viewport": {"width": 1366, "height": 900},
        "locale": "pt-BR",
    }
    if estado and Path(estado).exists():
        argumentos_contexto["storage_state"] = str(estado)
        logger.info("Reaproveitando a sessão guardada em %s", estado)

    contexto_navegador = navegador.new_context(**argumentos_contexto)
    contexto_navegador.set_default_timeout(timeout_ms)
    pagina = contexto_navegador.new_page()
    return pw, navegador, pagina


def fechar_navegador(pw, navegador) -> None:
    """Fecha tudo sem deixar processo órfão, mesmo se algo já quebrou."""
    for fechar in (
        lambda: navegador.close() if navegador else None,
        lambda: pw.stop() if pw else None,
    ):
        try:
            fechar()
        except Exception as e:  # pragma: no cover
            logger.debug("Erro ao fechar o navegador (ignorado): %s", e)


def capturar_estado(pagina) -> tuple[str | None, bytes | None]:
    """(html, screenshot) da página atual, para salvar quando der erro."""
    html = screenshot = None
    try:
        html = pagina.content()
    except Exception:
        pass
    try:
        screenshot = pagina.screenshot(full_page=True)
    except Exception:
        pass
    return html, screenshot
