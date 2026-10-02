// Tela "Novo pedido": máscaras, itens dinâmicos e resumo ao vivo.
// A validação oficial é feita no servidor ao salvar.
(function () {
  "use strict";

  const form = document.getElementById("form-pedido");
  if (!form) return;

  const listaItens = document.getElementById("itens");
  const modelo = document.getElementById("item-modelo");
  const vazio = document.getElementById("itens-vazio");

  const MAX_DIGITOS = { contato: 11, item_quantidade: 5, item_valor: 11, prazo_entrega: 6 };

  function digitos(texto) {
    return String(texto).replace(/\D/g, "");
  }

  // "11987654321" -> "(11) 98765-4321", formatando enquanto digita.
  function mascaraContato(d) {
    if (d.length === 0) return "";
    if (d.length <= 2) return "(" + d;
    if (d.length <= 7) return "(" + d.slice(0, 2) + ") " + d.slice(2);
    return "(" + d.slice(0, 2) + ") " + d.slice(2, 7) + "-" + d.slice(7);
  }

  // Prazo: "051026" -> "05/10/26", inserindo as barras enquanto digita (ou ao colar).
  function mascaraPrazo(d) {
    if (d.length <= 2) return d;
    if (d.length <= 4) return d.slice(0, 2) + "/" + d.slice(2);
    return d.slice(0, 2) + "/" + d.slice(2, 4) + "/" + d.slice(4);
  }

  // Mesmas regras do servidor: seis números formando uma data real (ano 20aa).
  function problemaDoPrazo(d) {
    if (d.length === 0) return "";
    if (d.length !== 6) return "Use o formato dd/mm/aa.";
    const dia = Number(d.slice(0, 2));
    const mes = Number(d.slice(2, 4));
    const ano = 2000 + Number(d.slice(4));
    const data = new Date(ano, mes - 1, dia);
    const real = data.getFullYear() === ano && data.getMonth() === mes - 1 && data.getDate() === dia;
    return real ? "" : "Data inválida.";
  }

  // Centavos (inteiro) -> "R$ 1.234,56".
  function formatarCentavos(centavos) {
    const texto = String(centavos).padStart(3, "0");
    const inteiro = texto.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return "R$ " + inteiro + "," + texto.slice(-2);
  }

  // Estilo bancário: os dígitos entram pela direita ("123456" -> "R$ 1.234,56").
  function mascaraMoeda(d) {
    return d.length === 0 ? "" : formatarCentavos(Number(d));
  }

  const MASCARAS = {
    contato: mascaraContato,
    item_quantidade: function (d) { return d; },
    item_valor: mascaraMoeda,
    prazo_entrega: mascaraPrazo,
  };

  function normalizar(nome, d) {
    d = d.slice(0, MAX_DIGITOS[nome]);
    if (nome === "item_valor") d = d.replace(/^0+(?=\d)/, "");
    return d;
  }

  function aplicarMascara(input, evento) {
    const nome = input.name;
    let d = normalizar(nome, digitos(input.value));
    // Apagar um caractere da máscara (")", "-", ",") apaga o dígito anterior.
    if (evento && evento.inputType && evento.inputType.startsWith("delete") && d === input.dataset.digitos) {
      d = d.slice(0, -1);
    }
    input.dataset.digitos = d;
    input.value = MASCARAS[nome](d);
    if (document.activeElement === input) {
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  function prepararCampos(raiz) {
    raiz.querySelectorAll('[name="contato"], [name="prazo_entrega"], [name="item_quantidade"], [name="item_valor"]').forEach(function (input) {
      if (input.value) aplicarMascara(input);
    });
  }

  function recalcular() {
    let quantidadeTotal = 0;
    let totalCentavos = 0;
    listaItens.querySelectorAll(".item").forEach(function (linha) {
      const qtd = Number(digitos(linha.querySelector('[name="item_quantidade"]').value) || 0);
      const valor = Number(digitos(linha.querySelector('[name="item_valor"]').value) || 0);
      const subtotal = qtd * valor;
      linha.querySelector(".item-subtotal output").textContent = formatarCentavos(subtotal);
      quantidadeTotal += qtd;
      totalCentavos += subtotal;
    });
    document.getElementById("resumo-quantidade").textContent = quantidadeTotal;
    document.getElementById("resumo-total").textContent = formatarCentavos(totalCentavos);
    vazio.hidden = listaItens.children.length > 0;
  }

  function limparErro(elemento) {
    const campo = elemento.closest(".tem-erro");
    if (!campo) return;
    campo.classList.remove("tem-erro");
    campo.removeAttribute("aria-describedby"); // apontava para a mensagem removida abaixo
    campo.querySelectorAll("[aria-invalid]").forEach(function (el) {
      el.removeAttribute("aria-invalid");
      el.removeAttribute("aria-describedby");
    });
    const msg = campo.querySelector(".campo-erro");
    if (msg) msg.remove();
  }

  document.getElementById("adicionar-item").addEventListener("click", function () {
    listaItens.appendChild(modelo.content.cloneNode(true));
    listaItens.lastElementChild.querySelector('[name="item_produto"]').focus();
    const erroItens = document.getElementById("itens-erro");
    if (erroItens) erroItens.remove();
    recalcular();
  });

  listaItens.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".item-remover");
    if (!botao) return;
    botao.closest(".item").remove();
    recalcular();
  });

  form.addEventListener("input", function (evento) {
    const alvo = evento.target;
    if (MASCARAS[alvo.name]) aplicarMascara(alvo, evento);
    limparErro(alvo);
    recalcular();
  });

  form.addEventListener("change", function (evento) {
    if (evento.target.type === "radio") limparErro(evento.target);
  });

  // Mensagem de erro no próprio campo, ligada a ele para leitores de tela.
  function marcarErro(input, texto) {
    const campo = input.closest(".campo");
    const id = input.id + "-erro";
    let aviso = document.getElementById(id);
    if (!aviso) {
      aviso = document.createElement("p");
      aviso.className = "campo-erro";
      aviso.id = id;
      campo.appendChild(aviso);
    }
    aviso.textContent = texto;
    campo.classList.add("tem-erro");
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", id);
  }

  form.addEventListener("focusout", function (evento) {
    const alvo = evento.target;
    // Quantidade mínima 1: vazio ou zero volta para 1 ao sair do campo.
    if (alvo.name === "item_quantidade" && Number(digitos(alvo.value)) < 1) {
      alvo.value = "1";
      alvo.dataset.digitos = "1";
      recalcular();
    }
    // Prazo incompleto ou data inexistente: avisa ao sair do campo (vazio só no envio).
    if (alvo.name === "prazo_entrega") {
      const problema = problemaDoPrazo(digitos(alvo.value));
      if (problema) marcarErro(alvo, problema);
    }
  });

  // Trocar a imagem apaga o aviso de imagem recusada pelo servidor.
  listaItens.addEventListener("imagemreferencia:alterada", function (evento) {
    const aviso = evento.target.querySelector(".item-imagem-erro");
    if (aviso) aviso.remove();
  });

  // ---------- Envio sem recarregar a página ----------
  // O formulário vai como multipart/form-data, com a imagem de cada item em item_imagem_{posição}.
  // A resposta é a própria página renderizada pelo servidor: dela são copiados o aviso, os campos
  // e os erros. Com erro, as linhas dos itens (e as imagens escolhidas) continuam na tela.

  const MENSAGEM_FALHA = "Não foi possível salvar o pedido agora. Tente novamente em instantes.";
  const MENSAGEM_SESSAO = "Sua sessão expirou. Entre novamente para salvar o pedido.";
  const CAMPOS_DO_ITEM = [".item-produto", ".item-quantidade", ".item-valor", ".item-subtotal"];
  const botaoSalvar = form.querySelector('button[type="submit"]');

  function importar(no) {
    return document.importNode(no, true);
  }

  function trocarAlerta(novo) {
    document.querySelectorAll(".pedido > .alerta").forEach(function (a) { a.remove(); });
    if (novo) form.before(novo);
  }

  function alertaDeErro(texto) {
    const alerta = document.createElement("div");
    alerta.className = "alerta alerta-erro";
    alerta.setAttribute("role", "alert");
    alerta.textContent = texto;
    return alerta;
  }

  function trocarItens(pagina, sucesso) {
    const novas = pagina.querySelectorAll("#itens > .item");
    if (sucesso) {
      listaItens.replaceChildren.apply(listaItens, Array.prototype.map.call(novas, importar));
      return;
    }
    // Erro: só os campos de texto de cada linha são trocados; a área da imagem fica intacta.
    listaItens.querySelectorAll(":scope > .item").forEach(function (linha, i) {
      const nova = pagina.querySelector('#itens > .item[data-indice="' + i + '"]');
      if (!nova) return; // linha em branco: o servidor a ignorou
      CAMPOS_DO_ITEM.forEach(function (seletor) {
        linha.querySelector(seletor).replaceWith(importar(nova.querySelector(seletor)));
      });
      const aviso = linha.querySelector(".item-imagem-erro");
      if (aviso) aviso.remove();
      const novoAviso = nova.querySelector(".item-imagem-erro");
      if (novoAviso) linha.querySelector(".item-imagem").appendChild(importar(novoAviso));
    });
  }

  function aplicarResposta(pagina, status) {
    const novoCsrf = pagina.getElementById("csrf-pedido");
    if (novoCsrf) document.getElementById("csrf-pedido").value = novoCsrf.value;
    trocarAlerta(pagina.querySelector(".pedido > .alerta"));
    if (status !== 200 && status !== 422) return; // falha ao gravar: a tela fica como está

    pagina.querySelectorAll("[data-regiao]").forEach(function (nova) {
      const atual = document.getElementById(nova.id);
      if (atual) atual.replaceWith(importar(nova));
    });
    const erroItens = document.getElementById("itens-erro");
    if (erroItens) erroItens.remove();
    const novoErroItens = pagina.getElementById("itens-erro");
    if (novoErroItens) document.getElementById("adicionar-item").before(importar(novoErroItens));

    trocarItens(pagina, status === 200);
    prepararCampos(form);
    recalcular();
  }

  async function enviar() {
    const dados = new FormData(form);
    const arquivos = window.ImagensReferencia ? window.ImagensReferencia.arquivos() : [];
    arquivos.forEach(function (arquivo, i) {
      if (arquivo) dados.append("item_imagem_" + i, arquivo, arquivo.name);
    });

    let resposta;
    try {
      resposta = await fetch(form.action, {
        method: "POST",
        body: dados,
        credentials: "same-origin",
        redirect: "manual", // sessão expirada vira redirecionamento para /entrar
      });
    } catch (erro) {
      trocarAlerta(alertaDeErro(MENSAGEM_FALHA));
      return;
    }
    if (resposta.type === "opaqueredirect") {
      trocarAlerta(alertaDeErro(MENSAGEM_SESSAO));
      return;
    }
    const pagina = new DOMParser().parseFromString(await resposta.text(), "text/html");
    if (!pagina.getElementById("form-pedido")) {
      trocarAlerta(alertaDeErro(MENSAGEM_FALHA));
      return;
    }
    aplicarResposta(pagina, resposta.status);
  }

  // Validação imediata no navegador: grupo de escolha obrigatório sem seleção ganha o mesmo
  // aviso que o servidor daria. O envio segue, e o servidor valida tudo de novo (todos os
  // campos de uma vez); a seleção feita depois apaga o aviso (limparErro).
  function marcarEscolhasPendentes() {
    form.querySelectorAll("fieldset.escolhas[data-obrigatorio]").forEach(function (grupo) {
      if (grupo.querySelector("input:checked") || grupo.classList.contains("tem-erro")) return;
      const nome = grupo.id.replace("grupo-", "");
      const aviso = document.createElement("p");
      aviso.className = "campo-erro";
      aviso.id = nome + "-erro";
      aviso.textContent = grupo.dataset.obrigatorio;
      grupo.appendChild(aviso);
      grupo.classList.add("tem-erro");
      grupo.setAttribute("aria-describedby", aviso.id);
    });
  }

  form.addEventListener("submit", function (evento) {
    if (!window.fetch || !window.FormData || !window.DOMParser) return; // envio tradicional
    evento.preventDefault();
    if (form.dataset.envio === "enviando") return;
    marcarEscolhasPendentes();

    form.dataset.envio = "enviando";
    form.setAttribute("aria-busy", "true");
    form.inert = true; // nada muda na tela enquanto o servidor responde
    botaoSalvar.textContent = "Salvando…";
    enviar()
      .catch(function () { trocarAlerta(alertaDeErro(MENSAGEM_FALHA)); })
      .finally(function () {
        form.inert = false;
        form.removeAttribute("aria-busy");
        botaoSalvar.textContent = "Salvar pedido";
        form.dataset.envio = "concluido";
        window.scrollTo(0, 0);
      });
  });

  prepararCampos(form);
  recalcular();
})();
