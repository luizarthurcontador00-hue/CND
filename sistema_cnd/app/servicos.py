"""Regras de negócio do painel: semáforo, contadores e janela de renovação.

Aqui mora a resposta para "esta certidão está OK ou não?". Isso é usado tanto
pela tela do Painel quanto pelo agendador para decidir o que precisa ser
emitido hoje.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import config
from app.modelos import (
    ROTULOS_STATUS,
    ROTULOS_TIPO,
    STATUS_DE_FALHA,
    CertidaoExigida,
    Emissao,
    Empresa,
    StatusEmissao,
    TipoCertidao,
)

# =============================================================================
#  CORES DO SEMÁFORO
# =============================================================================

VERDE = "verde"
AMARELO = "amarelo"
VERMELHO = "vermelho"
CINZA = "cinza"

#: Texto longo — usado nos contadores e nos filtros.
ROTULO_COR = {
    VERDE: "Válida",
    AMARELO: "Vencendo",
    VERMELHO: "Vencida ou positiva",
    CINZA: "Falha ou nunca consultada",
}

#: Texto curto — usado nas pastilhas da tabela, onde o detalhe já vem embaixo.
ROTULO_CURTO = {
    VERDE: "Válida",
    AMARELO: "Vencendo",
    VERMELHO: "Atenção",
    CINZA: "Sem certidão",
}

ORDEM_GRAVIDADE = {VERMELHO: 0, CINZA: 1, AMARELO: 2, VERDE: 3}


@dataclass
class Situacao:
    """Situação de uma empresa × certidão, já pronta para a tela."""

    tipo: str
    cor: str
    texto: str
    dias_para_vencer: int | None = None
    data_validade: date | None = None
    ultima_emissao: Emissao | None = None
    ultima_tentativa: Emissao | None = None
    pode_renovar_hoje: bool = False
    motivo_bloqueio: str | None = None

    @property
    def rotulo_tipo(self) -> str:
        try:
            return ROTULOS_TIPO[TipoCertidao(self.tipo)]
        except ValueError:
            return self.tipo

    @property
    def caminho_pdf(self) -> str | None:
        return self.ultima_emissao.caminho_pdf if self.ultima_emissao else None

    @property
    def precisa_atencao(self) -> bool:
        return self.cor in (VERMELHO, AMARELO, CINZA)


# =============================================================================
#  CÁLCULO DA SITUAÇÃO
# =============================================================================


def _dias_alerta() -> int:
    return int(config.agendamento.get("dias_alerta_amarelo", 15))


def calcular_situacao(
    tipo: str,
    emissoes_do_tipo: list[Emissao],
    hoje: date | None = None,
) -> Situacao:
    """Decide a cor do semáforo para uma empresa × certidão.

    Regras, na ordem:
      CINZA    — nunca consultada, ou a última tentativa falhou e não há
                 certidão válida em mãos.
      VERMELHO — certidão POSITIVA (há pendência), vencida, ou sem validade.
      AMARELO  — vence dentro do prazo de alerta (padrão: 15 dias).
      VERDE    — válida e com folga.

    Observação: se a última tentativa falhou MAS ainda existe certidão válida,
    a cor continua sendo a da certidão (verde/amarelo) e o texto avisa da falha.
    Assim uma instabilidade momentânea do site não pinta o painel de cinza.
    """
    hoje = hoje or date.today()
    alerta = _dias_alerta()

    if not emissoes_do_tipo:
        return Situacao(tipo=tipo, cor=CINZA, texto="Nunca consultada")

    # Já vêm ordenadas da mais recente para a mais antiga.
    ultima_tentativa = emissoes_do_tipo[0]
    ultima_com_documento = next((e for e in emissoes_do_tipo if e.tem_certidao), None)

    if ultima_com_documento is None:
        rotulo = ROTULOS_STATUS.get(ultima_tentativa.status_enum, ultima_tentativa.status)
        return Situacao(
            tipo=tipo,
            cor=CINZA,
            texto=rotulo,
            ultima_tentativa=ultima_tentativa,
        )

    validade = ultima_com_documento.data_validade
    dias = (validade - hoje).days if validade else None
    falha_recente = ultima_tentativa.falhou and ultima_tentativa.id != ultima_com_documento.id

    base = Situacao(
        tipo=tipo,
        cor=VERDE,
        texto="",
        dias_para_vencer=dias,
        data_validade=validade,
        ultima_emissao=ultima_com_documento,
        ultima_tentativa=ultima_tentativa,
    )

    # Positiva com pendência: vermelho independentemente da validade.
    if ultima_com_documento.status_enum == StatusEmissao.POSITIVA:
        base.cor = VERMELHO
        base.texto = "Positiva — há pendência"
        return base

    if validade is None:
        base.cor = VERMELHO
        base.texto = "Sem data de validade"
        return base

    if dias is not None and dias < 0:
        base.cor = VERMELHO
        base.texto = f"Vencida há {abs(dias)} dia(s)"
        return base

    if dias is not None and dias <= alerta:
        base.cor = AMARELO
        base.texto = "Vence hoje" if dias == 0 else f"Vence em {dias} dia(s)"
    else:
        base.cor = VERDE
        base.texto = f"Válida por mais {dias} dia(s)"

    if falha_recente:
        rotulo = ROTULOS_STATUS.get(ultima_tentativa.status_enum, ultima_tentativa.status)
        base.texto += f" • última tentativa: {rotulo.lower()}"

    return base


# =============================================================================
#  JANELA DE RENOVAÇÃO
# =============================================================================


def pode_renovar(tipo: str, situacao: Situacao, hoje: date | None = None) -> tuple[bool, str]:
    """A certidão pode/deve ser emitida hoje? Devolve (pode, motivo).

    Existe porque não adianta pedir renovação cedo demais: o FGTS, por exemplo,
    só aceita a partir do 10º dia anterior ao vencimento — antes disso o site
    simplesmente recusa. E emitir tudo todo dia derruba os sites.
    """
    hoje = hoje or date.today()
    regras = config.certidao(tipo)

    if not regras.get("ativo", True):
        return False, "Certidão desligada no config.yaml"

    if situacao.cor == CINZA and situacao.ultima_emissao is None:
        return True, "Nunca emitida"

    dias = situacao.dias_para_vencer
    if dias is None:
        return True, "Sem data de validade conhecida"

    if dias < 0:
        return True, "Vencida"

    janela = int(
        regras.get("janela_renovacao_dias")
        or config.agendamento.get("dias_antes_para_renovar", 30)
    )

    if dias <= janela:
        return True, f"Dentro da janela de renovação ({janela} dias)"

    faltam = dias - janela
    return False, f"Ainda cedo — a janela abre em {faltam} dia(s)"


# =============================================================================
#  MONTAGEM DO PAINEL
# =============================================================================


def emissoes_por_tipo(sessao: Session, empresa_id: int) -> dict[str, list[Emissao]]:
    """Todas as emissões de uma empresa, agrupadas por tipo, mais recente primeiro."""
    consulta = (
        select(Emissao)
        .where(Emissao.empresa_id == empresa_id)
        .order_by(Emissao.data_tentativa.desc(), Emissao.id.desc())
    )
    agrupadas: dict[str, list[Emissao]] = {}
    for emissao in sessao.scalars(consulta):
        agrupadas.setdefault(emissao.tipo_certidao, []).append(emissao)
    return agrupadas


@dataclass
class LinhaPainel:
    empresa: Empresa
    situacoes: list[Situacao]

    @property
    def pior_cor(self) -> str:
        if not self.situacoes:
            return CINZA
        return min((s.cor for s in self.situacoes), key=lambda c: ORDEM_GRAVIDADE[c])

    def situacao_de(self, tipo: str) -> Situacao | None:
        return next((s for s in self.situacoes if s.tipo == tipo), None)


def montar_painel(
    sessao: Session,
    apenas_ativas: bool = True,
    filtro_cor: str | None = None,
    filtro_empresa: str | None = None,
    filtro_tipo: str | None = None,
) -> tuple[list[LinhaPainel], dict[str, int]]:
    """Monta a tabela empresas × certidões e os contadores do topo."""
    consulta = select(Empresa).order_by(Empresa.razao_social)
    if apenas_ativas:
        consulta = consulta.where(Empresa.ativa.is_(True))

    if filtro_empresa:
        alvo = filtro_empresa.strip().lower()
        digitos = "".join(c for c in alvo if c.isdigit())
        empresas = [
            e
            for e in sessao.scalars(consulta)
            if alvo in e.razao_social.lower()
            or (e.nome_fantasia and alvo in e.nome_fantasia.lower())
            or (digitos and digitos in e.cnpj)
        ]
    else:
        empresas = list(sessao.scalars(consulta))

    linhas: list[LinhaPainel] = []
    contadores = {VERDE: 0, AMARELO: 0, VERMELHO: 0, CINZA: 0, "total": 0}

    for empresa in empresas:
        agrupadas = emissoes_por_tipo(sessao, empresa.id)
        situacoes: list[Situacao] = []

        for tipo in empresa.tipos_exigidos():
            if filtro_tipo and tipo.value != filtro_tipo:
                continue
            situacao = calcular_situacao(tipo.value, agrupadas.get(tipo.value, []))
            situacao.pode_renovar_hoje, situacao.motivo_bloqueio = pode_renovar(
                tipo.value, situacao
            )
            situacoes.append(situacao)

        if filtro_cor:
            situacoes = [s for s in situacoes if s.cor == filtro_cor]
            if not situacoes:
                continue

        for s in situacoes:
            contadores[s.cor] += 1
            contadores["total"] += 1

        linhas.append(LinhaPainel(empresa=empresa, situacoes=situacoes))

    return linhas, contadores


# =============================================================================
#  O QUE PRECISA SER EMITIDO
# =============================================================================


@dataclass
class Pendencia:
    """Uma emissão que o robô deveria fazer agora."""

    empresa: Empresa
    tipo: str
    motivo: str


def listar_pendencias(
    sessao: Session,
    empresa_ids: list[int] | None = None,
    tipos: list[str] | None = None,
    forcar: bool = False,
) -> tuple[list[Pendencia], list[Pendencia]]:
    """Devolve (a_emitir, puladas).

    forcar=True ignora a janela de renovação — é o que o botão "consultar
    agora" da tela usa quando você quer emitir na marra.
    """
    consulta = select(Empresa).where(Empresa.ativa.is_(True)).order_by(Empresa.razao_social)
    if empresa_ids:
        consulta = consulta.where(Empresa.id.in_(empresa_ids))

    a_emitir: list[Pendencia] = []
    puladas: list[Pendencia] = []

    for empresa in sessao.scalars(consulta):
        agrupadas = emissoes_por_tipo(sessao, empresa.id)
        for tipo in empresa.tipos_exigidos():
            if tipos and tipo.value not in tipos:
                continue

            regras = config.certidao(tipo.value)
            if not regras.get("ativo", True):
                puladas.append(
                    Pendencia(empresa, tipo.value, "Certidão desligada no config.yaml")
                )
                continue

            situacao = calcular_situacao(tipo.value, agrupadas.get(tipo.value, []))
            pode, motivo = pode_renovar(tipo.value, situacao)

            if pode or forcar:
                razao = motivo if pode else "Forçado pelo usuário"
                a_emitir.append(Pendencia(empresa, tipo.value, razao))
            else:
                puladas.append(Pendencia(empresa, tipo.value, motivo))

    return a_emitir, puladas


# =============================================================================
#  EXIGÊNCIAS PADRÃO
# =============================================================================


def exigencias_sugeridas(empresa: Empresa) -> list[TipoCertidao]:
    """Chute inicial de quais certidões a empresa precisa, no cadastro.

    Sempre editável na tela — é só um ponto de partida sensato.
    """
    sugeridas = [TipoCertidao.FEDERAL, TipoCertidao.CNDT, TipoCertidao.FGTS]
    if empresa.inscricao_estadual and (empresa.uf or "").upper() == "GO":
        sugeridas.append(TipoCertidao.ESTADUAL_GO)
    if empresa.inscricao_municipal:
        sugeridas.append(TipoCertidao.MUNICIPAL)
    return sugeridas


def aplicar_exigencias(sessao: Session, empresa: Empresa, tipos: list[str]) -> None:
    """Deixa as exigências da empresa exatamente iguais à lista informada.

    Não apaga registros: desliga (ativa=False) o que saiu da lista, para não
    perder o histórico de emissões já feitas daquele tipo.
    """
    desejados = set(tipos)
    existentes = {e.tipo_certidao: e for e in empresa.exigencias}

    for tipo, exigencia in existentes.items():
        exigencia.ativa = tipo in desejados

    for tipo in desejados - set(existentes):
        sessao.add(CertidaoExigida(empresa_id=empresa.id, tipo_certidao=tipo, ativa=True))
