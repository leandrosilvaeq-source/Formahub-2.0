// Tela "Novo pedido": máscaras, itens dinâmicos e resumo ao vivo.
// A validação oficial é feita no servidor ao salvar.
(function () {
  "use strict";

  const form = document.getElementById("form-pedido");
  if (!form) return;

  const listaItens = document.getElementById("itens");
  const modelo = document.getElementById("item-modelo");
  const vazio = document.getElementById("itens-vazio");

  const MAX_DIGITOS = { contato: 11, item_quantidade: 5, item_valor: 11 };

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
    raiz.querySelectorAll('[name="contato"], [name="item_quantidade"], [name="item_valor"]').forEach(function (input) {
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
    campo.querySelectorAll("[aria-invalid]").forEach(function (el) { el.removeAttribute("aria-invalid"); });
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

  // Quantidade mínima 1: vazio ou zero volta para 1 ao sair do campo.
  form.addEventListener("focusout", function (evento) {
    const alvo = evento.target;
    if (alvo.name === "item_quantidade" && Number(digitos(alvo.value)) < 1) {
      alvo.value = "1";
      alvo.dataset.digitos = "1";
      recalcular();
    }
  });

  prepararCampos(form);
  recalcular();
})();
