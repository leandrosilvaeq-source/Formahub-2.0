// Página Produção: move o pedido de etapa pelos botões do card ou arrastando-o para uma
// coluna permitida (os destinos são os dos botões: avançar e, em Em Produção, voltar à fila).
//
// O card só muda de coluna depois que o servidor confirma; numa falha ele fica onde estava.
// A etapa atual vai junto (etapa_esperada): se outro usuário já moveu o pedido, o servidor
// responde "conflito" e o quadro é recarregado. Quem move é sempre o usuário da sessão.
// "Entregue" pede confirmação. Nenhum dado é guardado no navegador.
(function () {
  "use strict";

  const pagina = document.querySelector(".producao");
  if (!pagina) return;

  const mensagem = document.getElementById("producao-mensagem");
  const dialogo = document.getElementById("confirmar-entrega");
  const FALHA = "Não foi possível mover o pedido agora. Tente novamente.";
  const SESSAO = "Sua sessão expirou. Entre novamente para mover o pedido.";
  let arrastado = null;
  let ocupado = false;

  function quadro() {
    return document.getElementById("quadro");
  }

  function coluna(etapa) {
    return quadro().querySelector('.coluna[data-etapa="' + etapa + '"]');
  }

  function botaoAvancar(card) {
    return card.querySelector(".producao-avancar");
  }

  function botaoPara(card, destino) {
    return card.querySelector('.producao-mover[data-destino="' + destino + '"]');
  }

  // Foco previsível: avançar o mesmo card (ou "Ver pedido", se ele terminou).
  function focoDoCard(card) {
    (botaoAvancar(card) || card.querySelector("a")).focus();
  }

  // Mensagem visível e anunciada (status para sucesso, alert para erro).
  function avisar(texto, tipo, tentarDeNovo) {
    const alerta = document.createElement("div");
    alerta.className = "alerta " + (tipo === "erro" ? "alerta-erro" : "alerta-sucesso");
    alerta.setAttribute("role", tipo === "erro" ? "alert" : "status");
    alerta.textContent = texto;
    mensagem.replaceChildren(alerta);
    if (tentarDeNovo) {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.className = "botao botao-primario producao-tentar";
      botao.textContent = "Tentar novamente";
      botao.addEventListener("click", function () { recarregarQuadro(); });
      mensagem.appendChild(botao);
    }
  }

  function atualizarContadores() {
    quadro().querySelectorAll(".coluna").forEach(function (col) {
      const total = col.querySelectorAll(".producao-card").length;
      col.querySelector("[data-contador]").textContent = total;
      col.querySelector(".coluna-contador .visualmente-oculto").textContent =
        total === 1 ? " pedido" : " pedidos";
      col.querySelector(".coluna-vazia").hidden = total > 0;
    });
  }

  // Dentro da etapa, do mais antigo para o mais recente (o id cresce com o cadastro).
  function inserirEmOrdem(lista, card) {
    const id = Number(card.dataset.pedido);
    const seguinte = Array.prototype.find.call(lista.children, function (outro) {
      return Number(outro.dataset.pedido) > id;
    });
    lista.insertBefore(card, seguinte || null);
  }

  // Botões da nova etapa, na ordem e com os estilos que o servidor informou.
  function trocarBotoes(card, movimentos) {
    const acoes = card.querySelector(".producao-card-acoes");
    acoes.querySelectorAll(".producao-mover").forEach(function (b) { b.remove(); });
    movimentos.forEach(function (m) {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.className = "botao producao-mover " +
        (m.retorno ? "botao-secundario producao-voltar" : "botao-primario producao-avancar");
      botao.dataset.destino = m.destino;
      botao.textContent = m.texto;
      botao.setAttribute("aria-label", m.texto + ": pedido de " + card.dataset.cliente);
      acoes.appendChild(botao);
    });
    if (movimentos.length) card.setAttribute("draggable", "true");
    else card.removeAttribute("draggable"); // Entregue: nada sai daqui
  }

  function aplicarMovimento(card, resposta) {
    card.dataset.etapa = resposta.etapa;
    trocarBotoes(card, resposta.movimentos);
    inserirEmOrdem(coluna(resposta.etapa).querySelector(".coluna-cards"), card);
    atualizarContadores();
    focoDoCard(card);
  }

  async function recarregarQuadro() {
    const atual = quadro();
    atual.classList.add("carregando");
    atual.setAttribute("aria-busy", "true");
    avisar("Atualizando o quadro…", "sucesso");
    try {
      const resposta = await fetch("/producao", { credentials: "same-origin", redirect: "manual" });
      if (resposta.type === "opaqueredirect") {
        avisar(SESSAO, "erro");
        return false;
      }
      const documento = new DOMParser().parseFromString(await resposta.text(), "text/html");
      const novo = documento.getElementById("quadro");
      if (!resposta.ok || !novo) throw new Error("quadro indisponível");
      atual.replaceWith(document.importNode(novo, true));
      mensagem.replaceChildren();
      return true;
    } catch (erro) {
      avisar("Não foi possível carregar a produção agora.", "erro", true);
      return false;
    } finally {
      atual.classList.remove("carregando");
      atual.removeAttribute("aria-busy");
    }
  }

  function confirmarEntrega(card) {
    const texto = "O pedido de " + card.dataset.cliente +
      " vai para Entregue. Nesta versão não é possível voltar a etapa.";
    if (!dialogo || typeof dialogo.showModal !== "function") {
      return Promise.resolve(window.confirm(texto));
    }
    if (dialogo.open) return Promise.resolve(false);
    document.getElementById("confirmar-entrega-texto").textContent = texto;
    dialogo.returnValue = "";
    const formulario = dialogo.querySelector("form");
    return new Promise(function (resolver) {
      // A resposta vem já no gesto (botão ou Esc), sem esperar o evento "close", que o
      // navegador entrega depois; o "close" só cobre outros jeitos de fechar a janela.
      let respondido = false;
      function responder(confirmado) {
        if (respondido) return;
        respondido = true;
        formulario.removeEventListener("submit", aoEnviar);
        dialogo.removeEventListener("cancel", aoCancelar);
        dialogo.removeEventListener("close", aoFechar);
        resolver(confirmado);
      }
      function aoEnviar(evento) {
        responder(!!evento.submitter && evento.submitter.value === "confirmar");
      }
      function aoCancelar() {
        responder(false);
      }
      function aoFechar() {
        if (!dialogo.open) responder(dialogo.returnValue === "confirmar");
      }
      formulario.addEventListener("submit", aoEnviar);
      dialogo.addEventListener("cancel", aoCancelar);
      dialogo.addEventListener("close", aoFechar);
      dialogo.showModal();
      dialogo.querySelector('[value="cancelar"]').focus(); // foco na opção segura
    });
  }

  async function mover(card, nova) {
    const botao = card && botaoPara(card, nova);
    if (ocupado || !botao) return;
    const etapaAtual = card.dataset.etapa;
    const cliente = card.dataset.cliente;
    if (nova === "entregue" && !(await confirmarEntrega(card))) {
      botao.focus();
      return;
    }

    ocupado = true;
    const rotulo = botao.textContent;
    botao.disabled = true;
    botao.textContent = "Movendo…";
    card.classList.add("movendo");
    card.setAttribute("aria-busy", "true");

    let resposta = null;
    let corpo = null;
    try {
      resposta = await fetch("/producao/" + card.dataset.pedido + "/mover", {
        method: "POST",
        body: new URLSearchParams({
          csrf: quadro().dataset.csrf,
          etapa_esperada: etapaAtual,
          nova_etapa: nova,
        }),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        redirect: "manual",
      });
      if (resposta.type !== "opaqueredirect") corpo = await resposta.json();
    } catch (erro) {
      corpo = null;
    }

    ocupado = false;
    card.classList.remove("movendo");
    card.removeAttribute("aria-busy");
    botao.disabled = false;
    botao.textContent = rotulo;

    if (corpo && corpo.ok) {
      aplicarMovimento(card, corpo);
      avisar("Pedido de " + cliente + " movido para " + corpo.titulo + ".", "sucesso");
      return;
    }
    if (resposta && resposta.type === "opaqueredirect") {
      avisar(SESSAO, "erro");
      botao.focus();
      return;
    }
    if (corpo && corpo.erro === "conflito") {
      if (await recarregarQuadro()) {
        avisar(corpo.mensagem, "erro");
        const recarregado = quadro().querySelector('.producao-card[data-pedido="' + card.dataset.pedido + '"]');
        if (recarregado) focoDoCard(recarregado);
      }
      return;
    }
    avisar((corpo && corpo.mensagem) || FALHA, "erro");
    botao.focus();
  }

  pagina.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".producao-mover");
    if (botao) mover(botao.closest(".producao-card"), botao.dataset.destino);
  });

  // Foto que não carregou (URL expirada, arquivo removido): some, sem deixar espaço vazio.
  pagina.addEventListener("error", function (evento) {
    if (evento.target.classList && evento.target.classList.contains("producao-card-foto")) {
      evento.target.remove();
    }
  }, true);
  pagina.querySelectorAll(".producao-card-foto").forEach(function (foto) {
    if (foto.complete && foto.naturalWidth === 0) foto.remove(); // falhou antes deste script
  });

  // ---------- Arrastar (desktop): só para as colunas dos botões do card ----------
  function destinosDe(card) {
    return Array.prototype.map.call(card.querySelectorAll(".producao-mover"), function (b) {
      return coluna(b.dataset.destino);
    });
  }

  function aceita(card, col) {
    return destinosDe(card).indexOf(col) !== -1;
  }

  function limparArraste() {
    if (arrastado) arrastado.classList.remove("arrastando");
    arrastado = null;
    const q = quadro();
    if (!q) return;
    q.classList.remove("arrastando");
    q.querySelectorAll(".destino-valido, .destino-ativo").forEach(function (col) {
      col.classList.remove("destino-valido", "destino-ativo");
    });
  }

  pagina.addEventListener("dragstart", function (evento) {
    const card = evento.target.closest && evento.target.closest(".producao-card[draggable]");
    if (!card || ocupado || !destinosDe(card).length) return;
    arrastado = card;
    evento.dataTransfer.effectAllowed = "move";
    evento.dataTransfer.setData("text/plain", card.dataset.pedido);
    card.classList.add("arrastando");
    quadro().classList.add("arrastando");
    destinosDe(card).forEach(function (col) { col.classList.add("destino-valido"); });
  });

  pagina.addEventListener("dragover", function (evento) {
    const col = evento.target.closest && evento.target.closest(".coluna");
    if (!arrastado || !col) return;
    if (aceita(arrastado, col)) {
      evento.preventDefault(); // só as etapas permitidas aceitam o card
      evento.dataTransfer.dropEffect = "move";
      col.classList.add("destino-ativo");
    } else {
      evento.dataTransfer.dropEffect = "none";
    }
  });

  pagina.addEventListener("dragleave", function (evento) {
    const col = evento.target.closest && evento.target.closest(".coluna");
    if (col && !col.contains(evento.relatedTarget)) col.classList.remove("destino-ativo");
  });

  pagina.addEventListener("drop", function (evento) {
    const col = evento.target.closest && evento.target.closest(".coluna");
    const card = arrastado;
    if (!card || !col || !aceita(card, col)) return;
    evento.preventDefault();
    limparArraste();
    mover(card, col.dataset.etapa);
  });

  pagina.addEventListener("dragend", limparArraste);
})();
