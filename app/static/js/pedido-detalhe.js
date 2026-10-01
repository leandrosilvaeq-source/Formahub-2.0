// Tela de detalhes do pedido: amplia a imagem de referência do item numa janela (dialog).
// Sem JavaScript (ou sem suporte a dialog) o link abre a imagem em outra aba.
// A URL é a assinada e temporária gerada pelo servidor; nada mais é buscado aqui.
(function () {
  "use strict";

  const janela = document.getElementById("imagem-ampliada");
  if (!janela || typeof janela.showModal !== "function") return;

  const imagem = janela.querySelector("img");
  const legenda = janela.querySelector("figcaption");
  let origem = null;

  document.addEventListener("click", function (evento) {
    const link = evento.target.closest("[data-ampliar]");
    if (!link) return;
    evento.preventDefault();
    origem = link;
    link.focus(); // o dialog devolve o foco a quem estava focado ao abrir
    imagem.src = link.href;
    imagem.alt = "Imagem de referência de " + link.dataset.legenda;
    legenda.textContent = link.dataset.legenda;
    janela.showModal();
  });

  // Ao fechar, a imagem sai da janela. A limpeza é feita já no gesto (botão, Esc, fundo),
  // sem esperar o evento "close", que o navegador entrega depois.
  function limpar() {
    imagem.removeAttribute("src");
  }

  janela.addEventListener("submit", limpar); // botão Fechar (form method="dialog")
  janela.addEventListener("cancel", limpar); // tecla Esc

  // Clique fora da imagem (no fundo escurecido) também fecha.
  janela.addEventListener("click", function (evento) {
    if (evento.target !== janela) return;
    limpar();
    janela.close();
  });

  janela.addEventListener("close", function () {
    limpar();
    if (origem) origem.focus();
  });
})();
