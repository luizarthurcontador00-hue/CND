"""Todas as telas e endpoints do sistema.

Telas:
    /            Painel      — semáforo empresas × certidões
    /empresas    Empresas    — cadastro, importação, quais certidões exige
    /consultas   Consultas   — botão "consultar agora" e acompanhamento
    /historico   Histórico   — todas as emissões, PDFs e upload manual
    /logs        Logs        — rodadas do robô e arquivos de depuração
"""

from __future__ import annotations

import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app import agendador, emissao as servico_emissao, importacao
from app.banco import obter_sessao
from app.config import RAIZ_PROJETO, config
from app.modelos import (
    ROTULOS_STATUS,
    ROTULOS_TIPO,
    CertidaoExigida,
    Emissao,
    Empresa,
    LogExecucao,
    OrigemEmissao,
    StatusEmissao,
    TipoCertidao,
)
from app.servicos import (
    AMARELO,
    CINZA,
    ROTULO_COR,
    ROTULO_CURTO,
    VERDE,
    VERMELHO,
    aplicar_exigencias,
    calcular_situacao,
    emissoes_por_tipo,
    exigencias_sugeridas,
    listar_pendencias,
    montar_painel,
)
from robos.base import cnpj_valido, limpar_cnpj, validade_com_fallback

logger = logging.getLogger(__name__)

rotas = APIRouter()

templates = Jinja2Templates(directory=str(RAIZ_PROJETO / "app" / "templates"))
templates.env.globals.update(
    ROTULOS_TIPO={t.value: r for t, r in ROTULOS_TIPO.items()},
    ROTULOS_STATUS={s.value: r for s, r in ROTULOS_STATUS.items()},
    ROTULO_COR=ROTULO_COR,
    ROTULO_CURTO=ROTULO_CURTO,
    TIPOS=[(t.value, ROTULOS_TIPO[t]) for t in TipoCertidao],
    CORES=[VERDE, AMARELO, VERMELHO, CINZA],
    versao="0.1.0",
)


def _formatar_data(valor) -> str:
    if not valor:
        return "—"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _formatar_cnpj(valor: str | None) -> str:
    c = limpar_cnpj(valor or "")
    if len(c) != 14:
        return valor or "—"
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


templates.env.filters["data"] = _formatar_data
templates.env.filters["cnpj"] = _formatar_cnpj


def _pagina(request: Request, template: str, **contexto) -> HTMLResponse:
    """Renderiza um template já com o que toda tela precisa."""
    contexto.setdefault("request", request)
    contexto.setdefault("mensagem", request.query_params.get("msg"))
    contexto.setdefault("tipo_mensagem", request.query_params.get("tipo", "sucesso"))
    contexto.setdefault("rodada_ativa", servico_emissao.rodada_em_andamento())
    return templates.TemplateResponse(template, contexto)


def _redirecionar(destino: str, msg: str | None = None, tipo: str = "sucesso"):
    if msg:
        destino = f"{destino}?{urlencode({'msg': msg, 'tipo': tipo})}"
    return RedirectResponse(destino, status_code=303)


# =============================================================================
#  TELA 1 — PAINEL
# =============================================================================


@rotas.get("/", response_class=HTMLResponse)
def painel(
    request: Request,
    cor: str | None = None,
    empresa: str | None = None,
    tipo: str | None = None,
    incluir_inativas: bool = False,
    sessao: Session = Depends(obter_sessao),
):
    linhas, contadores = montar_painel(
        sessao,
        apenas_ativas=not incluir_inativas,
        filtro_cor=cor,
        filtro_empresa=empresa,
        filtro_tipo=tipo,
    )

    # Colunas da tabela: só os tipos que alguma empresa realmente exige.
    tipos_usados: list[str] = []
    for linha in linhas:
        for situacao in linha.situacoes:
            if situacao.tipo not in tipos_usados:
                tipos_usados.append(situacao.tipo)
    ordem = [t.value for t in TipoCertidao]
    tipos_usados.sort(key=lambda t: ordem.index(t) if t in ordem else 99)

    return _pagina(
        request,
        "painel.html",
        titulo="Painel",
        linhas=linhas,
        contadores=contadores,
        tipos_usados=tipos_usados,
        filtro_cor=cor,
        filtro_empresa=empresa or "",
        filtro_tipo=tipo,
        incluir_inativas=incluir_inativas,
        proxima_rodada=agendador.proxima_execucao(),
        agendador_ativo=agendador.esta_ativo(),
    )


# =============================================================================
#  TELA 2 — EMPRESAS
# =============================================================================


@rotas.get("/empresas", response_class=HTMLResponse)
def listar_empresas(
    request: Request,
    busca: str | None = None,
    sessao: Session = Depends(obter_sessao),
):
    consulta = select(Empresa).order_by(Empresa.razao_social)
    empresas = list(sessao.scalars(consulta))

    if busca:
        alvo = busca.strip().lower()
        digitos = limpar_cnpj(alvo)
        empresas = [
            e
            for e in empresas
            if alvo in e.razao_social.lower()
            or (e.nome_fantasia and alvo in e.nome_fantasia.lower())
            or (digitos and digitos in e.cnpj)
        ]

    return _pagina(
        request,
        "empresas.html",
        titulo="Empresas",
        empresas=empresas,
        busca=busca or "",
    )


@rotas.get("/empresas/nova", response_class=HTMLResponse)
def nova_empresa(request: Request):
    return _pagina(
        request,
        "empresa_form.html",
        titulo="Nova empresa",
        empresa=None,
        exigencias=["FEDERAL", "CNDT", "FGTS"],
    )


@rotas.get("/empresas/{empresa_id}/editar", response_class=HTMLResponse)
def editar_empresa(
    request: Request, empresa_id: int, sessao: Session = Depends(obter_sessao)
):
    empresa = sessao.get(Empresa, empresa_id)
    if not empresa:
        return _redirecionar("/empresas", "Empresa não encontrada.", "erro")

    return _pagina(
        request,
        "empresa_form.html",
        titulo=f"Editar — {empresa.apelido}",
        empresa=empresa,
        exigencias=[e.tipo_certidao for e in empresa.exigencias if e.ativa],
    )


@rotas.post("/empresas/salvar")
def salvar_empresa(
    empresa_id: int | None = Form(None),
    cnpj: str = Form(...),
    razao_social: str = Form(...),
    nome_fantasia: str = Form(""),
    uf: str = Form(""),
    municipio: str = Form(""),
    inscricao_estadual: str = Form(""),
    inscricao_municipal: str = Form(""),
    regime_tributario: str = Form(""),
    observacoes: str = Form(""),
    ativa: str | None = Form(None),
    certidoes: list[str] = Form(default=[]),
    sessao: Session = Depends(obter_sessao),
):
    numero = limpar_cnpj(cnpj)
    if not cnpj_valido(numero):
        destino = f"/empresas/{empresa_id}/editar" if empresa_id else "/empresas/nova"
        return _redirecionar(
            destino, f"CNPJ {cnpj} é inválido — confira os números.", "erro"
        )

    duplicada = sessao.scalar(select(Empresa).where(Empresa.cnpj == numero))
    if duplicada and duplicada.id != empresa_id:
        return _redirecionar(
            "/empresas",
            f"Já existe empresa cadastrada com o CNPJ {_formatar_cnpj(numero)}: {duplicada.razao_social}.",
            "erro",
        )

    empresa = sessao.get(Empresa, empresa_id) if empresa_id else None
    criando = empresa is None
    if criando:
        empresa = Empresa(cnpj=numero)
        sessao.add(empresa)

    empresa.cnpj = numero
    empresa.razao_social = razao_social.strip()
    empresa.nome_fantasia = nome_fantasia.strip() or None
    empresa.uf = uf.strip().upper()[:2] or None
    empresa.municipio = municipio.strip() or None
    empresa.inscricao_estadual = inscricao_estadual.strip() or None
    empresa.inscricao_municipal = inscricao_municipal.strip() or None
    empresa.regime_tributario = regime_tributario.strip() or None
    empresa.observacoes = observacoes.strip() or None
    empresa.ativa = ativa is not None

    sessao.flush()

    escolhidas = certidoes or (
        [t.value for t in exigencias_sugeridas(empresa)] if criando else []
    )
    aplicar_exigencias(sessao, empresa, escolhidas)
    sessao.commit()

    verbo = "cadastrada" if criando else "atualizada"
    return _redirecionar("/empresas", f"Empresa {empresa.razao_social} {verbo} com sucesso.")


@rotas.post("/empresas/{empresa_id}/excluir")
def excluir_empresa(empresa_id: int, sessao: Session = Depends(obter_sessao)):
    empresa = sessao.get(Empresa, empresa_id)
    if not empresa:
        return _redirecionar("/empresas", "Empresa não encontrada.", "erro")

    nome = empresa.razao_social
    sessao.delete(empresa)
    sessao.commit()
    return _redirecionar(
        "/empresas",
        f"Empresa {nome} excluída. Os PDFs já emitidos continuam na pasta de certidões.",
    )


@rotas.post("/empresas/importar")
async def importar_empresas(
    arquivo: UploadFile = File(...), sessao: Session = Depends(obter_sessao)
):
    conteudo = await arquivo.read()
    if not conteudo:
        return _redirecionar("/empresas", "O arquivo enviado está vazio.", "erro")

    resultado = importacao.importar(sessao, conteudo, arquivo.filename or "")

    if resultado.erros and not resultado.total_processado:
        return _redirecionar("/empresas", "Nada importado. " + " | ".join(resultado.erros[:3]), "erro")

    mensagem = f"Importação concluída: {resultado.resumo}."
    if resultado.erros:
        mensagem += " Problemas: " + " | ".join(resultado.erros[:3])
        if len(resultado.erros) > 3:
            mensagem += f" (e mais {len(resultado.erros) - 3})"
        return _redirecionar("/empresas", mensagem, "aviso")

    return _redirecionar("/empresas", mensagem)


@rotas.get("/empresas/modelo-csv")
def baixar_modelo_csv():
    modelo = RAIZ_PROJETO.parent / "modelo_empresas.csv"
    if not modelo.exists():
        return _redirecionar("/empresas", "Modelo de planilha não encontrado.", "erro")
    return FileResponse(
        modelo, filename="modelo_empresas.csv", media_type="text/csv"
    )


# =============================================================================
#  TELA 3 — CONSULTAS
# =============================================================================


@rotas.get("/consultas", response_class=HTMLResponse)
def tela_consultas(request: Request, sessao: Session = Depends(obter_sessao)):
    empresas = list(
        sessao.scalars(
            select(Empresa).where(Empresa.ativa.is_(True)).order_by(Empresa.razao_social)
        )
    )
    a_emitir, puladas = listar_pendencias(sessao)

    return _pagina(
        request,
        "consultas.html",
        titulo="Consultas",
        empresas=empresas,
        a_emitir=a_emitir,
        puladas=puladas,
        progresso=servico_emissao.progresso_atual(),
        captcha_disponivel=config.captcha_disponivel,
    )


@rotas.post("/consultas/executar")
def executar_consultas(
    empresa_ids: list[int] = Form(default=[]),
    tipos: list[str] = Form(default=[]),
    forcar: str | None = Form(None),
):
    if servico_emissao.rodada_em_andamento():
        return _redirecionar("/consultas", "Já existe uma consulta em andamento.", "aviso")

    servico_emissao.iniciar_rodada(
        empresa_ids=empresa_ids or None,
        tipos=tipos or None,
        forcar=forcar is not None,
    )
    return _redirecionar("/consultas", "Consulta iniciada. Acompanhe o progresso abaixo.")


@rotas.get("/api/progresso")
def api_progresso():
    """Consultado pelo JavaScript da tela para atualizar a barra de progresso."""
    progresso = servico_emissao.progresso_atual()
    if progresso is None:
        return JSONResponse({"ativo": False})
    dados = progresso.como_dict()
    dados["ativo"] = not progresso.terminado
    return JSONResponse(dados)


# =============================================================================
#  TELA 4 — HISTÓRICO
# =============================================================================


@rotas.get("/historico", response_class=HTMLResponse)
def historico(
    request: Request,
    empresa_id: int | None = None,
    tipo: str | None = None,
    status: str | None = None,
    dias: int = 90,
    sessao: Session = Depends(obter_sessao),
):
    consulta = (
        select(Emissao)
        .order_by(desc(Emissao.data_tentativa), desc(Emissao.id))
        .limit(500)
    )
    if empresa_id:
        consulta = consulta.where(Emissao.empresa_id == empresa_id)
    if tipo:
        consulta = consulta.where(Emissao.tipo_certidao == tipo)
    if status:
        consulta = consulta.where(Emissao.status == status)
    if dias and dias > 0:
        consulta = consulta.where(
            Emissao.data_tentativa >= datetime.now() - timedelta(days=dias)
        )

    emissoes = list(sessao.scalars(consulta))
    empresas_por_id = {
        e.id: e for e in sessao.scalars(select(Empresa).order_by(Empresa.razao_social))
    }

    return _pagina(
        request,
        "historico.html",
        titulo="Histórico",
        emissoes=emissoes,
        empresas=list(empresas_por_id.values()),
        empresas_por_id=empresas_por_id,
        filtro_empresa=empresa_id,
        filtro_tipo=tipo,
        filtro_status=status,
        filtro_dias=dias,
        STATUS=[(s.value, ROTULOS_STATUS[s]) for s in StatusEmissao],
    )


@rotas.get("/historico/{emissao_id}/pdf")
def abrir_pdf(emissao_id: int, sessao: Session = Depends(obter_sessao)):
    registro = sessao.get(Emissao, emissao_id)
    if not registro or not registro.caminho_pdf:
        return _redirecionar("/historico", "Esta emissão não tem PDF salvo.", "erro")

    arquivo = Path(registro.caminho_pdf)
    if not arquivo.exists():
        return _redirecionar(
            "/historico",
            f"O arquivo {arquivo.name} não está mais na pasta de certidões.",
            "erro",
        )

    return FileResponse(arquivo, media_type="application/pdf", filename=arquivo.name)


@rotas.post("/historico/upload")
async def upload_manual(
    empresa_id: int = Form(...),
    tipo_certidao: str = Form(...),
    data_validade: str = Form(""),
    numero_certidao: str = Form(""),
    status: str = Form(StatusEmissao.NEGATIVA.value),
    arquivo: UploadFile = File(...),
    sessao: Session = Depends(obter_sessao),
):
    """Upload manual do PDF, para quando o robô não conseguir emitir.

    É a válvula de escape do sistema: mesmo sem serviço de captcha, o controle
    de validade continua completo porque você anexa o PDF emitido à mão.
    """
    empresa = sessao.get(Empresa, empresa_id)
    if not empresa:
        return _redirecionar("/historico", "Empresa não encontrada.", "erro")

    conteudo = await arquivo.read()
    if not conteudo:
        return _redirecionar("/historico", "O arquivo enviado está vazio.", "erro")

    from robos.base import caminho_pdf as montar_caminho

    destino = montar_caminho(config.pasta_certidoes, empresa.cnpj, tipo_certidao)
    destino.write_bytes(conteudo)

    # Tenta ler a validade de dentro do PDF; o campo do formulário tem prioridade.
    informada = None
    if data_validade.strip():
        try:
            informada = datetime.strptime(data_validade.strip(), "%Y-%m-%d").date()
        except ValueError:
            informada = None

    lida_emissao, lida_validade = validade_com_fallback(
        destino if destino.suffix.lower() == ".pdf" else None,
        config.certidao(tipo_certidao),
    )

    registro = Emissao(
        empresa_id=empresa.id,
        tipo_certidao=tipo_certidao,
        status=status,
        data_emissao=lida_emissao or date.today(),
        data_validade=informada or lida_validade,
        numero_certidao=numero_certidao.strip() or None,
        caminho_pdf=str(destino),
        origem=OrigemEmissao.MANUAL.value,
        mensagem_erro=None,
    )
    sessao.add(registro)
    sessao.commit()

    quando = registro.data_validade.strftime("%d/%m/%Y") if registro.data_validade else "não identificada"
    return _redirecionar(
        "/historico",
        f"PDF anexado a {empresa.apelido} ({ROTULOS_TIPO.get(TipoCertidao(tipo_certidao), tipo_certidao)}). Validade: {quando}.",
    )


@rotas.post("/historico/{emissao_id}/excluir")
def excluir_emissao(emissao_id: int, sessao: Session = Depends(obter_sessao)):
    registro = sessao.get(Emissao, emissao_id)
    if not registro:
        return _redirecionar("/historico", "Registro não encontrado.", "erro")
    sessao.delete(registro)
    sessao.commit()
    return _redirecionar("/historico", "Registro removido. O PDF continua na pasta.")


# =============================================================================
#  TELA 5 — LOGS
# =============================================================================


@rotas.get("/logs", response_class=HTMLResponse)
def logs(request: Request, sessao: Session = Depends(obter_sessao)):
    execucoes = list(
        sessao.scalars(select(LogExecucao).order_by(desc(LogExecucao.inicio)).limit(50))
    )

    arquivos_debug = []
    if config.pasta_debug.exists():
        arquivos = sorted(
            config.pasta_debug.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for arquivo in arquivos[:60]:
            if arquivo.is_file():
                arquivos_debug.append(
                    {
                        "nome": arquivo.name,
                        "tamanho_kb": max(1, arquivo.stat().st_size // 1024),
                        "quando": datetime.fromtimestamp(arquivo.stat().st_mtime),
                    }
                )

    return _pagina(
        request,
        "logs.html",
        titulo="Logs",
        execucoes=execucoes,
        arquivos_debug=arquivos_debug,
        pasta_debug=str(config.pasta_debug),
        agendador_ativo=agendador.esta_ativo(),
        proxima_rodada=agendador.proxima_execucao(),
    )


@rotas.get("/logs/{execucao_id}", response_class=HTMLResponse)
def detalhe_log(request: Request, execucao_id: int, sessao: Session = Depends(obter_sessao)):
    execucao = sessao.get(LogExecucao, execucao_id)
    if not execucao:
        return _redirecionar("/logs", "Execução não encontrada.", "erro")
    return _pagina(
        request, "log_detalhe.html", titulo=f"Execução #{execucao.id}", execucao=execucao
    )


@rotas.get("/logs/debug/arquivo/{nome}")
def abrir_debug(nome: str):
    """Abre um print de tela ou HTML salvo quando um site falhou."""
    # Só arquivos de dentro da pasta de debug — nada de subir diretórios.
    arquivo = (config.pasta_debug / Path(nome).name).resolve()
    if not str(arquivo).startswith(str(config.pasta_debug.resolve())) or not arquivo.exists():
        return _redirecionar("/logs", "Arquivo de depuração não encontrado.", "erro")

    tipos = {".png": "image/png", ".html": "text/html", ".txt": "text/plain"}
    return FileResponse(
        arquivo, media_type=tipos.get(arquivo.suffix.lower(), "application/octet-stream")
    )


# =============================================================================
#  SOBRE / DIAGNÓSTICO
# =============================================================================


@rotas.get("/sobre", response_class=HTMLResponse)
def sobre(request: Request, sessao: Session = Depends(obter_sessao)):
    total_empresas = sessao.scalar(select(func.count()).select_from(Empresa)) or 0
    total_emissoes = sessao.scalar(select(func.count()).select_from(Emissao)) or 0

    espaco_pdfs = 0
    quantidade_pdfs = 0
    if config.pasta_certidoes.exists():
        for pdf in config.pasta_certidoes.rglob("*.pdf"):
            espaco_pdfs += pdf.stat().st_size
            quantidade_pdfs += 1

    return _pagina(
        request,
        "sobre.html",
        titulo="Sobre o sistema",
        info={
            "Arquivo de configuração": str(config.arquivo),
            "Banco de dados": str(config.arquivo_banco),
            "Pasta das certidões": str(config.pasta_certidoes),
            "Pasta de logs": str(config.pasta_logs),
            "Navegador invisível (headless)": "sim" if config.navegador.get("headless") else "não",
            "Serviço de captcha": (
                f"{config.captcha.get('provedor')} (configurado)"
                if config.captcha_disponivel
                else "nenhum — Federal e FGTS exigem emissão manual"
            ),
            "Agendamento automático": "ligado" if agendador.esta_ativo() else "desligado",
            "Próxima rodada": _formatar_data(agendador.proxima_execucao()),
            "Empresas cadastradas": total_empresas,
            "Emissões registradas": total_emissoes,
            "PDFs guardados": f"{quantidade_pdfs} arquivo(s), {espaco_pdfs // 1024 // 1024} MB",
            "Espaço livre no disco": f"{shutil.disk_usage(config.pasta_certidoes.parent).free // 1024 // 1024 // 1024} GB",
        },
    )


@rotas.get("/saude")
def saude():
    """Verificação simples de que o programa está no ar."""
    return {"situacao": "ok", "quando": datetime.now().isoformat()}


# =============================================================================
#  PÁGINAS DE ERRO AMIGÁVEIS
# =============================================================================


def pagina_erro(
    request: Request, codigo: int, resumo: str, detalhe: str, emoji: str = "⚠️"
) -> HTMLResponse:
    """Erro em português e com caminho de volta, em vez de JSON cru."""
    resposta = templates.TemplateResponse(
        "erro.html",
        {
            "request": request,
            "titulo": f"Erro {codigo}",
            "resumo": resumo,
            "detalhe": detalhe,
            "emoji": emoji,
            "rodada_ativa": False,
        },
        status_code=codigo,
    )
    return resposta
