// Página Produção: move o pedido de etapa pelos botões do card ou arrastando-o para uma
// coluna permitida (os destinos são os dos botões: avançar e, em Em Produção, voltar à fila).
//
// O card só muda de coluna depois que o servidor confirma; numa falha ele fica onde estava.
// A etapa atual vai junto (etapa_esperada): se outro usuário já moveu o pedido, o servidor
// responde "conflito" e o quadro é recarregado. Quem move é sempre o usuário da sessão.
// "Entregue" pede confirmação. Nenhum dado é guardado no navegador.
//
// Cada card começa recolhido e pode ser expandido (só na tela: ao recarregar a página, volta
// recolhido), tem o
// comentário da produção (salvo à parte das observações do pedido) e o botão do status do
// pagamento, que alterna entre Pendente e Pago. Comentário e pagamento seguem a mesma regra
// do movimento: o valor atual vai junto e a tela só muda depois da confirmação do servidor.
// Recolhido, o card esconde os detalhes e as ações; o comentário continua editável.
//
// Pedido concluído (entregue e pago) sai do quadro: o banco já não o lista, e a tela o retira
// assim que o servidor confirma o pagamento (na coluna Entregue) ou a entrega (já pago).
(function () {
  "use strict";

  const pagina = document.querySelector(".producao");
  if (!pagina) return;

  const mensagem = document.getElementById("producao-mensagem");
  const dialogo = document.getElementById("confirmar-entrega");
  const FALHA = "Não foi possível mover o pedido agora. Tente novamente.";
  const SESSAO = "Sua sessão expirou. Entre novamente para mover o pedido.";
  const SESSAO_ALTERAR = "Sua sessão expirou. Entre novamente para salvar a alteração.";
  const FALHA_COMENTARIO = "Não foi possível salvar o comentário agora. Tente novamente.";
  const FALHA_PAGAMENTO = "Não foi possível alterar o pagamento agora. Tente novamente.";
  // Ícones do selo do pagamento (os mesmos do servidor).
  const ICONES_PAGAMENTO = {
    pago: '<circle cx="12" cy="12" r="9"/><path d="m7.8 12.4 2.8 2.8 5.6-5.8"/>',
    pendente: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  };
  let arrastado = null;
  let ocupado = false;
  // Cards expandidos nesta visita (ids); todos começam recolhidos. Só em memória: o quadro
  // atualizado mantém o estado, a página recarregada volta com tudo recolhido.
  const expandidos = new Set();
  // Comentários ocultados pelo botão do card recolhido (ids). Só em memória (nada vai ao banco):
  // o quadro atualizado mantém a escolha, a página recarregada volta com tudo visível.
  const comentariosOcultos = new Set();

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

  function cardDoPedido(id) {
    return quadro().querySelector('.producao-card[data-pedido="' + id + '"]');
  }

  // Foco previsível: avançar o mesmo card (ou "Ver pedido", se ele terminou); recolhido, o
  // botão de expandir.
  function focoDoCard(card) {
    if (card.classList.contains("recolhido")) {
      card.querySelector(".producao-recolher").focus();
      return;
    }
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

  // Mesma regra da função listar_producao do banco.
  function concluido(etapa, statusPagamento) {
    return etapa === "entregue" && statusPagamento === "pago";
  }

  function statusDoPagamento(card) {
    return card.querySelector(".producao-pagamento").dataset.status;
  }

  // Chamado só depois da confirmação do servidor. O foco vai para o card vizinho na mesma
  // coluna ou, se ela ficou vazia, para a mensagem de sucesso.
  function retirarConcluido(card) {
    const vizinho = card.nextElementSibling || card.previousElementSibling;
    const cliente = card.dataset.cliente;
    expandidos.delete(card.dataset.pedido);
    card.remove();
    atualizarContadores();
    avisar("Pedido de " + cliente + " concluído (entregue e pago) e retirado do quadro.", "sucesso");
    if (vizinho) {
      vizinho.querySelector(".producao-recolher").focus();
      return;
    }
    const alerta = mensagem.querySelector(".alerta");
    alerta.tabIndex = -1;
    alerta.focus();
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
      expandidos.forEach(function (id) {
        const card = cardDoPedido(id);
        if (card) definirRecolhido(card, false);
        else expandidos.delete(id);
      });
      comentariosOcultos.forEach(function (id) {
        const card = cardDoPedido(id);
        if (card && temComentarioSalvo(card)) aplicarComentario(card);
        else comentariosOcultos.delete(id);
      });
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
      if (concluido(corpo.etapa, statusDoPagamento(card))) {
        retirarConcluido(card);
        return;
      }
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
        const recarregado = cardDoPedido(card.dataset.pedido);
        if (recarregado) focoDoCard(recarregado);
      }
      return;
    }
    avisar((corpo && corpo.mensagem) || FALHA, "erro");
    botao.focus();
  }

  // ---------- Recolher e expandir ----------
  function definirRecolhido(card, recolher) {
    const botao = card.querySelector(".producao-recolher");
    const texto = recolher ? "Expandir pedido" : "Recolher pedido";
    card.classList.toggle("recolhido", recolher);
    // Detalhes e ações (as regiões do aria-controls); o comentário fica sempre à vista.
    botao.getAttribute("aria-controls").split(" ").forEach(function (id) {
      document.getElementById(id).hidden = recolher;
    });
    botao.setAttribute("aria-expanded", recolher ? "false" : "true");
    botao.title = texto;
    botao.querySelector(".visualmente-oculto").textContent = texto;
    if (recolher) expandidos.delete(card.dataset.pedido);
    else expandidos.add(card.dataset.pedido);
    aplicarComentario(card); // expandido: sempre à vista; recolhido: respeita a escolha anterior
  }

  // ---------- Ocultar e mostrar o comentário (card recolhido) ----------
  const ICONES_COMENTARIO = {
    ocultar: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/><path d="m4 4 16 16"/>',
    mostrar: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/>',
  };

  function temComentarioSalvo(card) {
    return card.querySelector(".producao-comentario-campo").dataset.salvo !== "";
  }

  // Estado do comentário no card: oculto só quando o card está recolhido, tem comentário salvo e
  // a pessoa escolheu ocultar. O botão (aria-expanded e nome) acompanha o que está à vista.
  function aplicarComentario(card) {
    const botao = card.querySelector(".producao-comentario-alternar");
    const conteudo = card.querySelector(".producao-comentario-conteudo");
    const salvo = temComentarioSalvo(card);
    if (!salvo) comentariosOcultos.delete(card.dataset.pedido); // sem comentário: nada a ocultar
    const oculto = comentariosOcultos.has(card.dataset.pedido);
    const texto = oculto ? "Mostrar comentário" : "Ocultar comentário";
    botao.hidden = !salvo;
    botao.setAttribute("aria-expanded", oculto ? "false" : "true");
    botao.title = texto;
    botao.querySelector(".visualmente-oculto").textContent = texto;
    botao.querySelector("svg").innerHTML = ICONES_COMENTARIO[oculto ? "mostrar" : "ocultar"];
    conteudo.hidden = oculto && card.classList.contains("recolhido");
  }

  function alternarComentario(card) {
    const id = card.dataset.pedido;
    if (comentariosOcultos.has(id)) comentariosOcultos.delete(id);
    else comentariosOcultos.add(id);
    aplicarComentario(card);
  }

  // ---------- Comentário da produção e status do pagamento ----------
  // Envia o formulário com o token da página; devolve {corpo}, {sessao: true} (sessão
  // expirada) ou {} (sem resposta).
  async function enviar(caminho, dados) {
    dados.csrf = quadro().dataset.csrf;
    try {
      const resposta = await fetch(caminho, {
        method: "POST",
        body: new URLSearchParams(dados),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        redirect: "manual",
      });
      if (resposta.type === "opaqueredirect") return { sessao: true };
      return { corpo: await resposta.json() };
    } catch (erro) {
      return {};
    }
  }

  // Mensagem do comentário, dentro do próprio card (status para sucesso, alert para erro).
  function avisarNoCard(card, texto, tipo) {
    const alerta = document.createElement("div");
    alerta.className = "alerta " + (tipo === "erro" ? "alerta-erro" : "alerta-sucesso");
    alerta.setAttribute("role", tipo === "erro" ? "alert" : "status");
    alerta.textContent = texto;
    card.querySelector(".producao-comentario-mensagem").replaceChildren(alerta);
  }

  async function salvarComentario(card) {
    const campo = card.querySelector(".producao-comentario-campo");
    const botao = card.querySelector(".producao-salvar-comentario");
    if (botao.disabled) return;
    const digitado = campo.value;
    botao.disabled = true;
    botao.textContent = "Salvando…";
    card.querySelector(".producao-comentario-mensagem").replaceChildren();

    const r = await enviar("/producao/" + card.dataset.pedido + "/comentario", {
      comentario_esperado: campo.dataset.salvo,
      comentario: digitado,
    });

    botao.disabled = false;
    botao.textContent = "Salvar comentário";
    const corpo = r.corpo;
    if (corpo && corpo.ok) {
      campo.dataset.salvo = corpo.comentario;
      if (campo.value === digitado) campo.value = corpo.comentario; // sem espaços nas pontas
      card.querySelector(".producao-comentario-info").textContent = corpo.auditoria;
      aplicarComentario(card); // comentário novo ou apagado: o botão aparece ou some
      avisarNoCard(card, corpo.mensagem, "sucesso");
      return;
    }
    if (r.sessao) {
      avisarNoCard(card, SESSAO_ALTERAR, "erro");
      botao.focus();
      return;
    }
    if (corpo && corpo.erro === "conflito") {
      // O quadro volta com o comentário mais recente; o texto digitado continua no campo (e o
      // card continua recolhido, se estava: o comentário fica à vista nos dois estados).
      if (await recarregarQuadro()) {
        const recarregado = cardDoPedido(card.dataset.pedido);
        if (recarregado) {
          const novoCampo = recarregado.querySelector(".producao-comentario-campo");
          comentariosOcultos.delete(recarregado.dataset.pedido); // mostra o texto e a mensagem
          aplicarComentario(recarregado);
          novoCampo.value = digitado;
          avisarNoCard(recarregado, corpo.mensagem, "erro");
          novoCampo.focus();
        } else {
          avisar("Este pedido não está mais no quadro.", "erro");
        }
      }
      return;
    }
    avisarNoCard(card, (corpo && corpo.mensagem) || FALHA_COMENTARIO, "erro");
    botao.focus();
  }

  function mostrarPagamento(botao, dados) {
    botao.dataset.status = dados.status;
    botao.className = "selo-status selo-status-" + dados.status + " producao-pagamento";
    botao.title = dados.acao;
    botao.setAttribute("aria-label", dados.rotulo + ". " + dados.acao);
    botao.querySelector("svg").innerHTML = ICONES_PAGAMENTO[dados.status];
    botao.querySelector(".producao-pagamento-texto").textContent = dados.rotulo;
  }

  async function alternarPagamento(botao) {
    if (botao.disabled) return;
    const card = botao.closest(".producao-card");
    const atual = botao.dataset.status;
    botao.disabled = true;
    botao.setAttribute("aria-busy", "true");

    const r = await enviar("/producao/" + card.dataset.pedido + "/pagamento", {
      status_esperado: atual,
      novo_status: atual === "pago" ? "pendente" : "pago",
    });

    botao.disabled = false;
    botao.removeAttribute("aria-busy");
    const corpo = r.corpo;
    if (corpo && corpo.ok) {
      if (concluido(card.dataset.etapa, corpo.status)) {
        retirarConcluido(card);
        return;
      }
      mostrarPagamento(botao, corpo);
      avisar("Pedido de " + card.dataset.cliente + ": " + corpo.mensagem, "sucesso");
      botao.focus();
      return;
    }
    if (r.sessao) {
      avisar(SESSAO_ALTERAR, "erro");
      botao.focus();
      return;
    }
    if (corpo && corpo.erro === "conflito") {
      if (await recarregarQuadro()) {
        avisar(corpo.mensagem, "erro");
        const recarregado = cardDoPedido(card.dataset.pedido);
        const novoBotao = recarregado && recarregado.querySelector(".producao-pagamento");
        if (novoBotao && !recarregado.classList.contains("recolhido")) novoBotao.focus();
      }
      return;
    }
    avisar((corpo && corpo.mensagem) || FALHA_PAGAMENTO, "erro");
    botao.focus();
  }

  pagina.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".producao-mover");
    if (botao) {
      mover(botao.closest(".producao-card"), botao.dataset.destino);
      return;
    }
    const alternar = evento.target.closest(".producao-recolher");
    if (alternar) {
      const card = alternar.closest(".producao-card");
      definirRecolhido(card, !card.classList.contains("recolhido"));
      return;
    }
    const comentario = evento.target.closest(".producao-comentario-alternar");
    if (comentario) {
      // Só ocultar/mostrar: não expande o card nem mexe em etapa, pagamento ou comentário.
      alternarComentario(comentario.closest(".producao-card"));
      return;
    }
    const pagamento = evento.target.closest(".producao-pagamento");
    if (pagamento) {
      alternarPagamento(pagamento);
      return;
    }
    const salvar = evento.target.closest(".producao-salvar-comentario");
    if (salvar) salvarComentario(salvar.closest(".producao-card"));
  });

  // Enquanto o comentário está em edição, o card não é arrastável: selecionar texto com o
  // mouse não pode virar arraste do card.
  pagina.addEventListener("focusin", function (evento) {
    if (!evento.target.classList.contains("producao-comentario-campo")) return;
    evento.target.closest(".producao-card").removeAttribute("draggable");
  });
  pagina.addEventListener("focusout", function (evento) {
    if (!evento.target.classList.contains("producao-comentario-campo")) return;
    const card = evento.target.closest(".producao-card");
    if (card.querySelector(".producao-mover")) card.setAttribute("draggable", "true");
  });

  // Apertar o botão de ocultar/mostrar o comentário não pode virar arraste do card: o card sai
  // de "arrastável" enquanto o ponteiro está apertado e volta ao soltar.
  let cardSemArraste = null;
  pagina.addEventListener("pointerdown", function (evento) {
    const botao = evento.target.closest && evento.target.closest(".producao-comentario-alternar");
    if (!botao) return;
    cardSemArraste = botao.closest(".producao-card");
    cardSemArraste.removeAttribute("draggable");
  });
  function devolverArraste() {
    const card = cardSemArraste;
    cardSemArraste = null;
    // Só este card, e não enquanto o comentário dele está em edição (ver focusin acima).
    const editando = document.activeElement.classList.contains("producao-comentario-campo");
    if (card && card.querySelector(".producao-mover") && !editando) {
      card.setAttribute("draggable", "true");
    }
  }
  document.addEventListener("pointerup", devolverArraste);
  document.addEventListener("pointercancel", devolverArraste);

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
    if (evento.target.closest(".producao-comentario")) return; // texto do comentário, não o card
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
