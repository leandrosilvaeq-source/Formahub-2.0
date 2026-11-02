// Tela Compras: o filtro de mês/ano envia o formulário ao trocar a opção (sem JavaScript fica o
// botão Filtrar) e as fotos da tabela ganham a prévia ampliada com lupa (foto-previa.js).
(function () {
  "use strict";

  const filtro = document.getElementById("compras-filtro");
  if (filtro) {
    filtro.querySelector("select").addEventListener("change", function () { filtro.submit(); });
    filtro.classList.add("com-js");
  }

  const tabela = document.getElementById("tabela-compras");
  if (tabela) window.FotoPrevia.iniciar(tabela, { lupa: true });
})();
