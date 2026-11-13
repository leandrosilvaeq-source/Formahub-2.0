// Tela Compras: os filtros de mês/ano e de local, a ordenação por data e as páginas atualizam o
// filtro, os cards e a tabela sem recarregar a tela (a página é buscada em segundo plano e só
// esses trechos são trocados). Sem JavaScript fica o botão Filtrar e os links normais. As fotos da
// tabela ganham a prévia ampliada com lupa (foto-previa.js).
(function () {
  "use strict";

  const filtro = document.getElementById("compras-filtro");
  const resumo = document.querySelector(".compras-resumo");
  const lista = document.getElementById("ultimas-compras");
  let ultimoPedido = 0;

  function atualizar(url) {
    const pedido = ++ultimoPedido;
    lista.setAttribute("aria-busy", "true");
    fetch(url, { headers: { Accept: "text/html" } })
      .then(function (resposta) {
        if (!resposta.ok || resposta.redirected) throw new Error("falha");
        return resposta.text();
      })
      .then(function (html) {
        if (pedido !== ultimoPedido) return; // uma escolha mais nova já está a caminho
        const nova = new DOMParser().parseFromString(html, "text/html");
        const novoFiltro = nova.getElementById("compras-filtro");
        const novoResumo = nova.querySelector(".compras-resumo");
        const novaLista = nova.getElementById("ultimas-compras");
        if (!novoFiltro || !novoResumo || !novaLista) throw new Error("falha");
        filtro.innerHTML = novoFiltro.innerHTML;
        resumo.innerHTML = novoResumo.innerHTML;
        resumo.setAttribute("aria-label", novoResumo.getAttribute("aria-label"));
        lista.innerHTML = novaLista.innerHTML;
        lista.removeAttribute("aria-busy");
        window.history.replaceState(null, "", url);
      })
      .catch(function () {
        if (pedido === ultimoPedido) window.location.href = url; // recarga normal como reserva
      });
  }

  if (filtro && resumo && lista) {
    filtro.classList.add("com-js");
    filtro.addEventListener("change", function () {
      const dados = new URLSearchParams(new FormData(filtro));
      atualizar(filtro.getAttribute("action") + "?" + dados.toString());
    });
    filtro.addEventListener("submit", function (evento) { evento.preventDefault(); });

    // Ordenação e páginas também atualizam só a lista.
    lista.addEventListener("click", function (evento) {
      const link = evento.target.closest('a[href^="/compras?"]');
      if (!link || evento.ctrlKey || evento.metaKey || evento.shiftKey || evento.button !== 0) return;
      evento.preventDefault();
      atualizar(link.getAttribute("href"));
    });
  }

  // A prévia das fotos fica na seção (que não é trocada), então continua valendo após cada filtro.
  if (lista) window.FotoPrevia.iniciar(lista, { lupa: true });
})();
