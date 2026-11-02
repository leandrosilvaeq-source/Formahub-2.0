// Tela Custos, tabelas de acessórios e embalagens: a foto de referência abre a mesma prévia
// ampliada com lupa da tela Compras (foto-previa.js): mouse, teclado, toque e Esc.
(function () {
  "use strict";

  const secao = document.getElementById("insumos");
  if (secao && window.FotoPrevia) window.FotoPrevia.iniciar(secao, { lupa: true });

  // Histórico de compras no custo unitário: janela simples (data, quantidade e valor), acima da
  // tabela (posição fixa, para não ser cortada pelo quadro rolável e sem deslocar nada). Mouse e
  // foco abrem; sair fecha; toque alterna; Esc fecha. A janela fica aberta enquanto o mouse a cobre.
  const MARGEM = 8;
  let janela = null;
  let gatilho = null;
  let atraso = null;
  let devolvendoFoco = false; // o foco devolvido após o Esc não deve reabrir a janela

  function fecharJanela() {
    window.clearTimeout(atraso);
    if (!janela) return;
    janela.remove();
    janela = null;
    if (gatilho) gatilho.removeAttribute("aria-describedby");
    gatilho = null;
  }

  function fecharDepois() {
    window.clearTimeout(atraso);
    atraso = window.setTimeout(fecharJanela, 150);
  }

  function abrirJanela(botao) {
    window.clearTimeout(atraso);
    if (gatilho === botao && janela) return;
    fecharJanela();
    const dados = botao.parentElement.querySelector(".custos-hist-dados");
    if (!dados) return;
    janela = document.createElement("div");
    janela.className = "custos-hist";
    janela.id = "custos-hist-aberto";
    janela.setAttribute("role", "tooltip");
    janela.innerHTML = dados.innerHTML;
    janela.addEventListener("pointerenter", function () { window.clearTimeout(atraso); });
    janela.addEventListener("pointerleave", fecharDepois);
    document.body.append(janela);
    gatilho = botao;
    botao.setAttribute("aria-describedby", janela.id);
    // Abaixo do valor; sem espaço, acima. Sempre dentro da tela.
    const r = botao.getBoundingClientRect();
    const largura = Math.min(janela.offsetWidth, window.innerWidth - 2 * MARGEM);
    let esquerda = Math.min(Math.max(MARGEM, r.right - largura), window.innerWidth - largura - MARGEM);
    let topo = r.bottom + 4;
    const altura = janela.offsetHeight;
    if (topo + altura > window.innerHeight - MARGEM) topo = r.top - altura - 4;
    topo = Math.max(MARGEM, Math.min(topo, window.innerHeight - altura - MARGEM)); // nunca fora da tela
    janela.style.left = Math.round(esquerda) + "px";
    janela.style.top = Math.round(topo) + "px";
    janela.style.maxHeight = window.innerHeight - 2 * MARGEM + "px";
  }

  if (secao) {
    secao.addEventListener("pointerover", function (e) {
      if (e.pointerType !== "mouse") return;
      const botao = e.target.closest(".custos-hist-gatilho");
      if (botao) abrirJanela(botao);
    });
    secao.addEventListener("pointerout", function (e) {
      if (e.pointerType !== "mouse") return;
      const botao = e.target.closest(".custos-hist-gatilho");
      if (botao && !botao.contains(e.relatedTarget)) fecharDepois();
    });
    secao.addEventListener("focusin", function (e) {
      const botao = e.target.closest(".custos-hist-gatilho");
      if (botao && !devolvendoFoco && botao.matches(":focus-visible")) abrirJanela(botao);
    });
    secao.addEventListener("focusout", function (e) {
      if (e.target.closest(".custos-hist-gatilho")) fecharDepois();
    });
    secao.addEventListener("click", function (e) { // toque, Enter ou Espaço: alterna
      const botao = e.target.closest(".custos-hist-gatilho");
      if (!botao) return;
      if (janela && gatilho === botao) fecharJanela();
      else abrirJanela(botao);
    });
    document.addEventListener("pointerdown", function (e) {
      if (janela && !janela.contains(e.target) && !(gatilho && gatilho.contains(e.target))) fecharJanela();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && janela) {
        const volta = gatilho;
        fecharJanela();
        if (volta) {
          devolvendoFoco = true;
          volta.focus();
          devolvendoFoco = false;
        }
      }
    });
    window.addEventListener("resize", fecharJanela);
    window.addEventListener("scroll", fecharJanela, true);
  }
})();
