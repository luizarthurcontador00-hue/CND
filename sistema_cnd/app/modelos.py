"""Modelo de dados do sistema (tabelas do banco.db).

Quatro tabelas principais:
    empresas            -> cadastro dos clientes do escritório
    certidoes_exigidas  -> quais certidões cada empresa precisa (é configurável)
    emissoes            -> cada tentativa de emissão e seu resultado
    logs_execucao       -> cada rodada do robô (+ logs_execucao_itens, o detalhe)
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# =============================================================================
#  LISTAS FIXAS (usadas como texto no banco, para o arquivo ficar legível)
# =============================================================================


class TipoCertidao(str, Enum):
    """Tipos de certidão que o sistema conhece.

    Para acrescentar uma nova (outro estado, outra prefeitura):
      1. adicione aqui,
      2. crie o módulo em robos/,
      3. registre em robos/registro.py,
      4. acrescente a seção em config.yaml.
    Nada mais no sistema precisa mudar.
    """

    FEDERAL = "FEDERAL"
    CNDT = "CNDT"
    FGTS = "FGTS"
    ESTADUAL_GO = "ESTADUAL_GO"
    MUNICIPAL = "MUNICIPAL"


class StatusEmissao(str, Enum):
    """Resultado de uma tentativa de emissão."""

    NEGATIVA = "NEGATIVA"
    POSITIVA_COM_EFEITO_NEGATIVA = "POSITIVA_COM_EFEITO_NEGATIVA"
    POSITIVA = "POSITIVA"
    ERRO_SITE = "ERRO_SITE"
    CAPTCHA_FALHOU = "CAPTCHA_FALHOU"
    SEM_PENDENCIA_MAS_NAO_EMITIU = "SEM_PENDENCIA_MAS_NAO_EMITIU"


#: Status em que existe uma certidão válida em mãos.
STATUS_COM_CERTIDAO = {
    StatusEmissao.NEGATIVA,
    StatusEmissao.POSITIVA_COM_EFEITO_NEGATIVA,
    StatusEmissao.POSITIVA,
}

#: Status que representam falha do robô (nenhum documento foi obtido).
STATUS_DE_FALHA = {
    StatusEmissao.ERRO_SITE,
    StatusEmissao.CAPTCHA_FALHOU,
    StatusEmissao.SEM_PENDENCIA_MAS_NAO_EMITIU,
}


class OrigemEmissao(str, Enum):
    ROBO = "ROBO"
    MANUAL = "MANUAL"  # PDF enviado à mão pela tela de Histórico


class TipoExecucao(str, Enum):
    AGENDADA = "AGENDADA"
    MANUAL = "MANUAL"


#: Rótulos em português para exibir na tela.
ROTULOS_STATUS = {
    StatusEmissao.NEGATIVA: "Negativa",
    StatusEmissao.POSITIVA_COM_EFEITO_NEGATIVA: "Positiva com efeito de negativa",
    StatusEmissao.POSITIVA: "Positiva (há pendência)",
    StatusEmissao.ERRO_SITE: "Erro no site",
    StatusEmissao.CAPTCHA_FALHOU: "Captcha não resolvido",
    StatusEmissao.SEM_PENDENCIA_MAS_NAO_EMITIU: "Sem pendência, mas não emitiu",
}

ROTULOS_TIPO = {
    TipoCertidao.FEDERAL: "Federal (RFB/PGFN)",
    TipoCertidao.CNDT: "Trabalhista (CNDT)",
    TipoCertidao.FGTS: "FGTS (CRF)",
    TipoCertidao.ESTADUAL_GO: "Estadual (SEFAZ-GO)",
    TipoCertidao.MUNICIPAL: "Municipal",
}


# =============================================================================
#  TABELAS
# =============================================================================


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(14), unique=True, index=True, nullable=False)
    razao_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nome_fantasia: Mapped[str | None] = mapped_column(String(255))
    uf: Mapped[str | None] = mapped_column(String(2))
    municipio: Mapped[str | None] = mapped_column(String(120))
    inscricao_estadual: Mapped[str | None] = mapped_column(String(40))
    inscricao_municipal: Mapped[str | None] = mapped_column(String(40))
    regime_tributario: Mapped[str | None] = mapped_column(String(40))
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    observacoes: Mapped[str | None] = mapped_column(Text)

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now())

    exigencias: Mapped[list["CertidaoExigida"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan", lazy="selectin"
    )
    emissoes: Mapped[list["Emissao"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan"
    )

    # ------------------------------------------------------------------ ajudas
    @property
    def cnpj_formatado(self) -> str:
        c = self.cnpj
        if len(c) != 14:
            return c
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"

    @property
    def e_matriz(self) -> bool:
        """Matriz é o CNPJ terminado em /0001. Filial é /0002, /0003..."""
        return len(self.cnpj) == 14 and self.cnpj[8:12] == "0001"

    @property
    def cnpj_matriz(self) -> str:
        """CNPJ da matriz do mesmo grupo (a Federal é emitida pela matriz).

        Os dígitos verificadores são recalculados: aproveitar os da filial
        geraria um CNPJ inválido, recusado pelos sites do governo.
        """
        from robos.base import cnpj_da_matriz

        return cnpj_da_matriz(self.cnpj)

    @property
    def apelido(self) -> str:
        return self.nome_fantasia or self.razao_social

    def tipos_exigidos(self) -> list[TipoCertidao]:
        return [
            TipoCertidao(e.tipo_certidao)
            for e in sorted(self.exigencias, key=lambda x: x.tipo_certidao)
            if e.ativa
        ]

    def __repr__(self) -> str:
        return f"<Empresa {self.cnpj} {self.razao_social!r}>"


class CertidaoExigida(Base):
    """Quais certidões a empresa precisa.

    Não é fixo: empresa sem inscrição estadual não puxa estadual, empresa sem
    funcionário pode não precisar de FGTS, e a municipal depende da prefeitura.
    """

    __tablename__ = "certidoes_exigidas"
    __table_args__ = (UniqueConstraint("empresa_id", "tipo_certidao", name="uq_empresa_tipo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(
        ForeignKey("empresas.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tipo_certidao: Mapped[str] = mapped_column(String(30), nullable=False)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    empresa: Mapped["Empresa"] = relationship(back_populates="exigencias")

    def __repr__(self) -> str:
        return f"<CertidaoExigida empresa={self.empresa_id} {self.tipo_certidao}>"


class Emissao(Base):
    """Uma tentativa de emissão e o que ela produziu."""

    __tablename__ = "emissoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(
        ForeignKey("empresas.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tipo_certidao: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False)

    data_emissao: Mapped[date | None] = mapped_column(Date)
    data_validade: Mapped[date | None] = mapped_column(Date, index=True)
    numero_certidao: Mapped[str | None] = mapped_column(String(120))
    caminho_pdf: Mapped[str | None] = mapped_column(Text)
    mensagem_erro: Mapped[str | None] = mapped_column(Text)
    html_debug: Mapped[str | None] = mapped_column(Text)

    origem: Mapped[str] = mapped_column(String(10), default=OrigemEmissao.ROBO.value)
    data_tentativa: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )
    duracao_segundos: Mapped[float | None] = mapped_column()

    empresa: Mapped["Empresa"] = relationship(back_populates="emissoes")

    # ------------------------------------------------------------------ ajudas
    @property
    def status_enum(self) -> StatusEmissao:
        return StatusEmissao(self.status)

    @property
    def tem_certidao(self) -> bool:
        return self.status_enum in STATUS_COM_CERTIDAO

    @property
    def falhou(self) -> bool:
        return self.status_enum in STATUS_DE_FALHA

    @property
    def dias_para_vencer(self) -> int | None:
        if not self.data_validade:
            return None
        return (self.data_validade - date.today()).days

    @property
    def vencida(self) -> bool:
        dias = self.dias_para_vencer
        return dias is not None and dias < 0

    def __repr__(self) -> str:
        return f"<Emissao {self.tipo_certidao} empresa={self.empresa_id} {self.status}>"


class LogExecucao(Base):
    """Cabeçalho de uma rodada do robô."""

    __tablename__ = "logs_execucao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), default=TipoExecucao.MANUAL.value)
    inicio: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    fim: Mapped[datetime | None] = mapped_column(DateTime)

    total_consultas: Mapped[int] = mapped_column(Integer, default=0)
    total_sucesso: Mapped[int] = mapped_column(Integer, default=0)
    total_falha: Mapped[int] = mapped_column(Integer, default=0)
    total_pulado: Mapped[int] = mapped_column(Integer, default=0)
    observacao: Mapped[str | None] = mapped_column(Text)

    itens: Mapped[list["LogExecucaoItem"]] = relationship(
        back_populates="execucao", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def duracao_segundos(self) -> float | None:
        if not self.fim:
            return None
        return (self.fim - self.inicio).total_seconds()

    @property
    def em_andamento(self) -> bool:
        return self.fim is None


class LogExecucaoItem(Base):
    """Resultado de uma empresa × certidão dentro de uma rodada."""

    __tablename__ = "logs_execucao_itens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execucao_id: Mapped[int] = mapped_column(
        ForeignKey("logs_execucao.id", ondelete="CASCADE"), index=True, nullable=False
    )
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id", ondelete="SET NULL"))
    empresa_nome: Mapped[str | None] = mapped_column(String(255))
    cnpj: Mapped[str | None] = mapped_column(String(14))
    tipo_certidao: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40))
    mensagem: Mapped[str | None] = mapped_column(Text)
    duracao_segundos: Mapped[float | None] = mapped_column()
    tentativas: Mapped[int] = mapped_column(Integer, default=1)
    quando: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    execucao: Mapped["LogExecucao"] = relationship(back_populates="itens")
