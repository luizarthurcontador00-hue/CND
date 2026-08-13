/* =========================================================================
   Sistema de CNDs — JavaScript da interface.
   Pouco e simples de propósito: sem framework e sem build.
   ========================================================================= */

/** Confirmação antes de ações que não têm volta. */
document.addEventListener('submit', function (evento) {
  const formulario = evento.target;
  const pergunta = formulario.dataset.confirmar;
  if (pergunta && !window.confirm(pergunta)) {
    evento.preventDefault();
  }
});

/** Marca/desmarca todas as caixas de uma lista. */
function marcarTodas(seletor, marcado) {
  document.querySelectorAll(seletor).forEach(function (caixa) {
    caixa.checked = marcado;
  });
}

/** Formata o CNPJ enquanto o usuário digita. */
document.querySelectorAll('input[data-mascara="cnpj"]').forEach(function (campo) {
  campo.addEventListener('input', function () {
    let v = campo.value.replace(/\D/g, '').slice(0, 14);
    if (v.length > 12) v = v.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{0,2}).*/, '$1.$2.$3/$4-$5');
    else if (v.length > 8) v = v.replace(/^(\d{2})(\d{3})(\d{3})(\d{0,4})/, '$1.$2.$3/$4');
    else if (v.length > 5) v = v.replace(/^(\d{2})(\d{3})(\d{0,3})/, '$1.$2.$3');
    else if (v.length > 2) v = v.replace(/^(\d{2})(\d{0,3})/, '$1.$2');
    campo.value = v;
  });
});

/** Acompanhamento da rodada de consultas (tela Consultas). */
function acompanharProgresso() {
  const painel = document.getElementById('painel-progresso');
  if (!painel) return;

  const barra = document.getElementById('barra-progresso');
  const rotulo = document.getElementById('rotulo-progresso');
  const lista = document.getElementById('lista-progresso');
  const botao = document.getElementById('botao-consultar');

  const rotulosStatus = {
    NEGATIVA: 'Negativa',
    POSITIVA_COM_EFEITO_NEGATIVA: 'Positiva com efeito de negativa',
    POSITIVA: 'Positiva (há pendência)',
    ERRO_SITE: 'Erro no site',
    CAPTCHA_FALHOU: 'Captcha não resolvido',
    SEM_PENDENCIA_MAS_NAO_EMITIU: 'Sem pendência, mas não emitiu'
  };
  const coresStatus = {
    NEGATIVA: 'verde',
    POSITIVA_COM_EFEITO_NEGATIVA: 'verde',
    POSITIVA: 'vermelho',
    ERRO_SITE: 'vermelho',
    CAPTCHA_FALHOU: 'amarelo',
    SEM_PENDENCIA_MAS_NAO_EMITIU: 'amarelo'
  };

  let paradas = 0;

  function atualizar() {
    fetch('/api/progresso')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.execucao_id && !d.ativo) {
          painel.classList.add('oculto');
          return;
        }

        painel.classList.remove('oculto');
        const pct = d.porcentagem || 0;
        barra.style.width = Math.max(pct, 4) + '%';
        barra.textContent = pct + '%';

        if (d.ativo) {
          rotulo.textContent = 'Consultando ' + (d.concluidos + 1) + ' de ' + d.total +
            (d.atual ? ' — ' + d.atual : '');
          if (botao) botao.disabled = true;
        } else {
          rotulo.textContent = 'Concluído: ' + d.concluidos + ' de ' + d.total + ' consulta(s).';
          if (botao) botao.disabled = false;
        }

        lista.innerHTML = '';
        (d.itens || []).slice().reverse().forEach(function (item) {
          const div = document.createElement('div');
          const cor = coresStatus[item.status] || 'cinza';
          div.innerHTML =
            '<span class="etiqueta ' + cor + '">' + (rotulosStatus[item.status] || item.status) + '</span> ' +
            '<strong>' + item.empresa + '</strong> — ' + item.tipo +
            (item.mensagem ? ' <span class="linha-secundaria">(' + item.mensagem + ')</span>' : '');
          lista.appendChild(div);
        });

        if (d.erro_fatal) {
          rotulo.textContent = 'Erro na rodada: ' + d.erro_fatal;
        }

        if (!d.ativo) {
          paradas += 1;
          // Depois de terminar, recarrega a página uma vez para atualizar as listas.
          if (paradas === 1 && d.total > 0) {
            setTimeout(function () { window.location.reload(); }, 2500);
          }
        }
      })
      .catch(function () { /* servidor reiniciando — tenta de novo no próximo ciclo */ });
  }

  atualizar();
  setInterval(atualizar, 2000);
}

document.addEventListener('DOMContentLoaded', acompanharProgresso);
