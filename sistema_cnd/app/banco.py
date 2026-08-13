"""Conexão com o banco SQLite (arquivo único, sem servidor)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import config
from app.modelos import Base

logger = logging.getLogger(__name__)

_caminho = config.arquivo_banco
_caminho.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{_caminho}",
    # A interface web e o agendador rodam em threads diferentes.
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _ajustar_sqlite(conexao, _registro) -> None:
    """WAL evita travamento quando a tela lê enquanto o robô grava."""
    cursor = conexao.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


CriarSessao = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def criar_tabelas() -> None:
    Base.metadata.create_all(engine)
    logger.info("Banco de dados pronto em %s", _caminho)


@contextmanager
def sessao() -> Iterator[Session]:
    """Sessão com commit automático no fim e rollback em caso de erro.

    Uso:
        with sessao() as s:
            s.add(empresa)
    """
    s = CriarSessao()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def obter_sessao() -> Iterator[Session]:
    """Dependência do FastAPI (usada com Depends)."""
    s = CriarSessao()
    try:
        yield s
    finally:
        s.close()


def caminho_banco() -> str:
    return str(_caminho)


def engine_atual() -> Engine:
    return engine
