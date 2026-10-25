// Lista de pedidos: altera o status do pagamento (botão) e o da produção (seleção) na própria
// tabela, pelos mesmos endpoints da Produção. A linha só muda depois da confirmação do servidor.
// O valor atual vai junto (status_esperado / etapa_esperada): se outro usuário já alterou o
// pedido, o servidor responde "conflito" e a página é recarregada. Nada fica guardado no navegador.
(function () {
  "use strict";

  const tabela = document.getElementById("tabela-pedidos");
  if (!tabela) return;

  const mensagem = document.getElementById("pedidos-mensagem");
  const SESSAO = "Sua sessão expirou. Entre novamente para salvar a alteração.";
  const FALHA_PAGAMENTO = "Não foi possível alterar o pagamento agora. Tente novamente.";
  const FALHA_ETAPA = "Não foi possível mover o pedido agora. Tente novamente.";
  const ICONES_PAGAMENTO = {
    pago: '<circle cx="12" cy="12" r="9"/><path d="m7.8 12.4 2.8 2.8 5.6-5.8"/>',
    pendente: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  };

  function avisar(texto, tipo) {
    const alerta = document.createElement("div");
    alerta.className = "alerta " + (tipo === "erro" ? "alerta-erro" : "alerta-sucesso");
    alerta.setAttribute("role", tipo === "erro" ? "alert" : "status");
    alerta.textContent = texto;
    mensagem.replaceChildren(alerta);
  }

  // Envia o formulário com o token da página; devolve {corpo}, {sessao: true} ou {}.
  async function enviar(caminho, dados) {
    dados.csrf = tabela.dataset.csrf;
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

  // Outro usuário alterou o pedido: mostra a mensagem e recarrega a lista com o valor atual.
  function recarregarComAviso(texto) {
    avisar(texto + " Atualizando a lista…", "erro");
    window.setTimeout(function () { window.location.reload(); }, 1500);
  }

  async function alternarPagamento(botao) {
    if (botao.disabled) return;
    const linha = botao.closest("tr");
    const atual = botao.dataset.status;
    botao.disabled = true;
    botao.setAttribute("aria-busy", "true");

    const r = await enviar("/producao/" + linha.dataset.pedido + "/pagamento", {
      status_esperado: atual,
      novo_status: atual === "pago" ? "pendente" : "pago",
    });

    botao.disabled = false;
    botao.removeAttribute("aria-busy");
    const corpo = r.corpo;
    if (corpo && corpo.ok) {
      botao.dataset.status = corpo.status;
      botao.className = "selo-status selo-status-" + corpo.status + " producao-pagamento";
      botao.title = corpo.acao;
      botao.setAttribute("aria-label",
        corpo.rotulo + ". " + corpo.acao + ": pedido de " + linha.dataset.cliente);
      botao.querySelector("svg").innerHTML = ICONES_PAGAMENTO[corpo.status];
      botao.querySelector(".producao-pagamento-texto").textContent = corpo.rotulo;
      avisar("Pedido de " + linha.dataset.cliente + ": " + corpo.mensagem, "sucesso");
      botao.focus();
      return;
    }
    if (r.sessao) {
      avisar(SESSAO, "erro");
    } else if (corpo && corpo.erro === "conflito") {
      recarregarComAviso(corpo.mensagem);
    } else {
      avisar((corpo && corpo.mensagem) || FALHA_PAGAMENTO, "erro");
    }
    botao.focus();
  }

  async function mudarEtapa(selecao) {
    const linha = selecao.closest("tr");
    const atual = selecao.dataset.etapa;
    const nova = selecao.value;
    if (nova === atual) return;

    const rotuloNovo = selecao.options[selecao.selectedIndex].textContent;
    if (nova === "entregue" && !window.confirm(
      "O pedido de " + linha.dataset.cliente + " vai para " + rotuloNovo +
      ". Nesta versão não é possível voltar a etapa.")) {
      selecao.value = atual;
      return;
    }

    selecao.disabled = true;
    selecao.setAttribute("aria-busy", "true");
    const r = await enviar("/producao/" + linha.dataset.pedido + "/mover", {
      etapa_esperada: atual,
      nova_etapa: nova,
    });
    selecao.removeAttribute("aria-busy");
    const corpo = r.corpo;

    if (corpo && corpo.ok) {
      // As opções mudam com a etapa e as contagens do filtro também: recarrega a lista.
      try {
        window.sessionStorage.setItem("pedidos-aviso",
          "Pedido de " + linha.dataset.cliente + " movido para " + rotuloNovo + ".");
      } catch (erro) { /* sem armazenamento: só não mostra o aviso depois de recarregar */ }
      window.location.reload();
      return;
    }
    selecao.disabled = false;
    selecao.value = atual; // numa falha a linha fica onde estava
    if (r.sessao) {
      avisar(SESSAO, "erro");
    } else if (corpo && corpo.erro === "conflito") {
      recarregarComAviso(corpo.mensagem);
    } else {
      avisar((corpo && corpo.mensagem) || FALHA_ETAPA, "erro");
    }
    selecao.focus();
  }

  try {
    const aviso = window.sessionStorage.getItem("pedidos-aviso");
    if (aviso) {
      window.sessionStorage.removeItem("pedidos-aviso");
      avisar(aviso, "sucesso");
    }
  } catch (erro) { /* ignorado */ }

  tabela.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".producao-pagamento");
    if (botao) alternarPagamento(botao);
  });

  tabela.addEventListener("change", function (evento) {
    const selecao = evento.target.closest(".pedidos-etapa");
    if (selecao) mudarEtapa(selecao);
  });

  // Filtros por coluna, estilo Excel: o botão do título abre uma lista de valores com caixas de
  // seleção. Só escondem linhas já carregadas; combinam entre si e com o filtro de status do
  // topo da página. Coluna sem filtro = todos os valores marcados.
  const semResultado = document.getElementById("tabela-sem-resultado");
  const linhas = Array.from(tabela.querySelectorAll("tbody tr"));
  const botoes = Array.from(tabela.querySelectorAll(".filtro-botao"));
  const selecionados = {}; // chave da coluna -> Set de valores marcados (ausente = sem filtro)
  const SEM_VALOR = "(Vazio)";

  function texto(celula) { return celula.textContent.replace(/\s+/g, " ").trim(); }

  function valorDaLinha(linha, chave) {
    const c = linha.cells;
    // Colunas: 0 Foto, 1 Cliente, 2 Produto, 3 Quantidade, 4 Pagamento, 5 Valor Total, 6 Prazo,
    // 7 Status Pagamento, 8 Status Entrega, 9 Observações, 10 Ações.
    if (chave === "cliente") return texto(c[1]);
    if (chave === "produto") return texto(c[2]);
    if (chave === "pagamento") return texto(c[4]);
    if (chave === "prazo") { // aaaa-mm-dd: a lista do filtro fica em ordem de data
      const data = c[6].querySelector("time");
      return data ? data.dateTime : "";
    }
    if (chave === "status_pagamento") {
      const botao = linha.querySelector(".producao-pagamento");
      return botao ? botao.dataset.status : "";
    }
    if (chave === "etapa") return linha.dataset.etapa;
    return texto(c[9]); // observacoes
  }

  function aplicarFiltros() {
    let visiveis = 0;
    linhas.forEach(function (linha) {
      const mostra = Object.keys(selecionados).every(function (chave) {
        return selecionados[chave].has(valorDaLinha(linha, chave));
      });
      linha.hidden = !mostra;
      if (mostra) visiveis += 1;
    });
    semResultado.hidden = visiveis > 0;
    botoes.forEach(function (botao) {
      const ativo = botao.dataset.filtro in selecionados;
      botao.classList.toggle("ativo", ativo);
      botao.title = ativo ? "Filtro aplicado" : "Filtrar";
    });
  }

  // Valores da coluna: lista fixa (status) ou os valores distintos presentes na tabela.
  function opcoesDaColuna(botao) {
    if (botao.dataset.opcoes) {
      return JSON.parse(botao.dataset.opcoes).map(function (o) {
        return { valor: o[0], rotulo: o[1] };
      });
    }
    const vistos = new Set();
    linhas.forEach(function (linha) { vistos.add(valorDaLinha(linha, botao.dataset.filtro)); });
    return Array.from(vistos)
      .sort(function (a, b) { return a.localeCompare(b, "pt-BR", { sensitivity: "base" }); })
      .map(function (v) { return { valor: v, rotulo: rotuloDoValor(botao.dataset.filtro, v) }; });
  }

  function rotuloDoValor(chave, valor) {
    if (!valor) return SEM_VALOR;
    if (chave === "prazo") { // 2026-10-05 -> 05/10/26 (como na tabela)
      const p = valor.split("-");
      return p[2] + "/" + p[1] + "/" + p[0].slice(2);
    }
    return valor;
  }

  function normalizar(t) {
    return t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  let painel = null;
  let botaoAberto = null;

  function fecharPainel(devolverFoco) {
    if (!painel) return;
    painel.remove();
    painel = null;
    botaoAberto.setAttribute("aria-expanded", "false");
    if (devolverFoco) botaoAberto.focus();
    botaoAberto = null;
  }

  function abrirPainel(botao) {
    fecharPainel(false);
    const chave = botao.dataset.filtro;
    const opcoes = opcoesDaColuna(botao);
    const marcados = chave in selecionados
      ? selecionados[chave]
      : new Set(opcoes.map(function (o) { return o.valor; }));

    painel = document.createElement("div");
    painel.className = "filtro-painel";
    painel.setAttribute("role", "dialog");
    painel.setAttribute("aria-label", "Filtrar por " + botao.dataset.titulo);

    const busca = document.createElement("input");
    busca.type = "search";
    busca.className = "campo-texto filtro-busca";
    busca.placeholder = "Buscar";
    busca.setAttribute("aria-label", "Buscar valores de " + botao.dataset.titulo);

    const lista = document.createElement("div");
    lista.className = "filtro-lista";

    function linhaOpcao(rotulo, marcado) {
      const label = document.createElement("label");
      label.className = "filtro-opcao";
      const caixa = document.createElement("input");
      caixa.type = "checkbox";
      caixa.checked = marcado;
      const span = document.createElement("span");
      span.textContent = rotulo;
      span.title = rotulo;
      label.append(caixa, span);
      return { label: label, caixa: caixa };
    }

    const todos = linhaOpcao("(Selecionar tudo)", false);
    todos.label.classList.add("filtro-todos");
    const itens = opcoes.map(function (o) {
      const item = linhaOpcao(o.rotulo, marcados.has(o.valor));
      item.opcao = o;
      item.busca = normalizar(o.rotulo);
      return item;
    });
    lista.append(todos.label);
    itens.forEach(function (i) { lista.append(i.label); });

    function visiveisAgora() { return itens.filter(function (i) { return !i.label.hidden; }); }

    function atualizarTodos() {
      const v = visiveisAgora();
      const n = v.filter(function (i) { return i.caixa.checked; }).length;
      todos.caixa.checked = v.length > 0 && n === v.length;
      todos.caixa.indeterminate = n > 0 && n < v.length;
    }

    function confirmar() {
      const escolhidos = new Set(itens
        .filter(function (i) { return i.caixa.checked; })
        .map(function (i) { return i.opcao.valor; }));
      if (escolhidos.size === itens.length) delete selecionados[chave];
      else selecionados[chave] = escolhidos;
      aplicarFiltros();
    }

    busca.addEventListener("input", function () {
      const termo = normalizar(busca.value.trim());
      itens.forEach(function (i) { i.label.hidden = termo !== "" && !i.busca.includes(termo); });
      atualizarTodos();
    });
    lista.addEventListener("change", function (evento) {
      if (evento.target === todos.caixa) {
        visiveisAgora().forEach(function (i) { i.caixa.checked = todos.caixa.checked; });
      }
      atualizarTodos();
      confirmar();
    });

    const limpar = document.createElement("button");
    limpar.type = "button";
    limpar.className = "filtro-limpar";
    limpar.textContent = "Limpar filtro";
    limpar.addEventListener("click", function () {
      delete selecionados[chave];
      aplicarFiltros();
      fecharPainel(true);
    });

    painel.append(busca, lista, limpar);
    document.body.append(painel);
    atualizarTodos();

    const r = botao.getBoundingClientRect();
    const largura = painel.offsetWidth;
    painel.style.top = Math.round(r.bottom + 2) + "px";
    painel.style.left = Math.max(8, Math.min(r.left, window.innerWidth - largura - 8)) + "px";
    botao.setAttribute("aria-expanded", "true");
    botaoAberto = botao;
    busca.focus();
  }

  tabela.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".filtro-botao");
    if (!botao) return;
    if (botao === botaoAberto) fecharPainel(true);
    else abrirPainel(botao);
  });
  document.addEventListener("click", function (evento) {
    if (painel && !painel.contains(evento.target) && !evento.target.closest(".filtro-botao")) {
      fecharPainel(false);
    }
  });
  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape" && painel) fecharPainel(true);
  });
  window.addEventListener("resize", function () { fecharPainel(false); });
  window.addEventListener("scroll", function () { fecharPainel(false); }, true);

  // Foto que não carregou (URL expirada): some, sem deixar espaço vazio.
  tabela.addEventListener("error", function (evento) {
    if (evento.target.classList && evento.target.classList.contains("pedido-tabela-foto")) {
      evento.target.remove();
    }
  }, true);
})();
