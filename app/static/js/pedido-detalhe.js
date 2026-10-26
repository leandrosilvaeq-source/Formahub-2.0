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

// Comentário da produção: salvo à parte das observações do pedido. O texto anterior vai junto
// (comentario_esperado) para o servidor detectar alteração feita por outro usuário.
(function () {
  "use strict";

  const bloco = document.getElementById("comentario-producao");
  if (!bloco) return;

  const campo = bloco.querySelector(".producao-comentario-campo");
  const botao = bloco.querySelector(".producao-salvar-comentario");
  const info = bloco.querySelector(".producao-comentario-info");
  const mensagem = bloco.querySelector(".producao-comentario-mensagem");

  function avisar(texto, tipo) {
    const alerta = document.createElement("div");
    alerta.className = "alerta " + (tipo === "erro" ? "alerta-erro" : "alerta-sucesso");
    alerta.setAttribute("role", tipo === "erro" ? "alert" : "status");
    alerta.textContent = texto;
    mensagem.replaceChildren(alerta);
  }

  botao.addEventListener("click", async function () {
    if (botao.disabled) return;
    const digitado = campo.value;
    botao.disabled = true;
    botao.textContent = "Salvando…";
    mensagem.replaceChildren();
    let resposta = null;
    let corpo = null;
    try {
      resposta = await fetch("/producao/" + bloco.dataset.pedido + "/comentario", {
        method: "POST",
        body: new URLSearchParams({
          csrf: bloco.dataset.csrf,
          comentario_esperado: campo.dataset.salvo,
          comentario: digitado,
        }),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        redirect: "manual",
      });
      if (resposta.type !== "opaqueredirect") corpo = await resposta.json();
    } catch (erro) {
      corpo = null;
    }
    botao.disabled = false;
    botao.textContent = "Salvar comentário";
    if (corpo && corpo.ok) {
      campo.dataset.salvo = corpo.comentario;
      if (campo.value === digitado) campo.value = corpo.comentario; // sem espaços nas pontas
      info.textContent = corpo.auditoria;
      avisar(corpo.mensagem, "sucesso");
    } else if (resposta && resposta.type === "opaqueredirect") {
      avisar("Sua sessão expirou. Entre novamente para salvar a alteração.", "erro");
    } else {
      avisar((corpo && corpo.mensagem) || "Não foi possível salvar o comentário agora. Tente novamente.", "erro");
    }
  });
})();
