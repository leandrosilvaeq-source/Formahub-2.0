// Tela de entrada: mostrar/ocultar senha. Sem o script, a senha continua oculta e o botão não aparece.
(function () {
  "use strict";

  document.querySelectorAll(".senha-alternar").forEach(function (botao) {
    const campo = document.getElementById(botao.dataset.alvo);
    if (!campo) return;
    botao.hidden = false;
    botao.addEventListener("click", function () {
      const mostrar = campo.type === "password";
      campo.type = mostrar ? "text" : "password";
      botao.textContent = mostrar ? "Ocultar" : "Mostrar";
      botao.setAttribute("aria-pressed", mostrar ? "true" : "false");
      campo.focus();
    });
  });

  // Ao sair da página com a senha visível, volta a ocultá-la (por exemplo, no botão "voltar").
  window.addEventListener("pagehide", function () {
    document.querySelectorAll(".senha-alternar").forEach(function (botao) {
      const campo = document.getElementById(botao.dataset.alvo);
      if (campo) campo.type = "password";
    });
  });
})();
