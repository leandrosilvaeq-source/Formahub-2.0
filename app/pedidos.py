"""Tela de novo pedido: leitura do formulário, validação e cálculos.

Nesta versão nada é gravado; o pedido só é validado.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

FORMAS_PAGAMENTO = ["PIX", "Dinheiro", "Cartão"]
FORMAS_ENTREGA = ["Entrega em mãos", "Retirada"]

DIGITOS_CONTATO = 11
SO_MASCARA_CONTATO = re.compile(r"^[\d()\s-]*$")


def somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def formatar_contato(texto: str) -> str:
    """'11987654321' -> '(11) 98765-4321' (formata o que houver, como a máscara da tela)."""
    d = somente_digitos(texto)[:DIGITOS_CONTATO]
    if len(d) <= 2:
        return f"({d}" if d else ""
    if len(d) <= 7:
        return f"({d[:2]}) {d[2:]}"
    return f"({d[:2]}) {d[2:7]}-{d[7:]}"


def parse_moeda(texto: str) -> Decimal | None:
    """Converte 'R$ 1.234,56', '1234,56' ou '1234.56' em Decimal."""
    texto = texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        return None
    return valor if valor.is_finite() else None


def formatar_brl(valor: Decimal) -> str:
    """Decimal('1234.5') -> 'R$ 1.234,50'."""
    sinal = "-" if valor < 0 else ""
    inteiro, centavos = f"{abs(valor):.2f}".split(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"{sinal}R$ {inteiro},{centavos}"


@dataclass
class Item:
    produto: str = ""
    quantidade: str = "1"
    valor_unitario: str = ""
    subtotal: Decimal = Decimal("0")
    erros: dict[str, str] = field(default_factory=dict)


@dataclass
class Pedido:
    cliente: str = ""
    contato: str = ""
    pagamento: str = ""
    entrega: str = ""
    observacoes: str = ""
    itens: list[Item] = field(default_factory=list)
    quantidade_total: int = 0
    total: Decimal = Decimal("0")
    erros: dict[str, str] = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return not self.erros and not any(item.erros for item in self.itens)


def pedido_vazio() -> Pedido:
    return Pedido(itens=[Item()])


def ler_formulario(dados: dict[str, list[str]]) -> Pedido:
    """Monta o pedido a partir do formulário (valores como listas, estilo parse_qs)."""

    def campo(nome: str) -> str:
        return dados.get(nome, [""])[0].strip()

    produtos = dados.get("item_produto", [])
    quantidades = dados.get("item_quantidade", [])
    valores = dados.get("item_valor", [])
    linhas = [
        Item(
            produto=produtos[i].strip() if i < len(produtos) else "",
            quantidade=quantidades[i].strip() if i < len(quantidades) else "",
            valor_unitario=valores[i].strip() if i < len(valores) else "",
        )
        for i in range(max(len(produtos), len(quantidades), len(valores)))
    ]
    # Linhas totalmente em branco são ignoradas; se todas estiverem em branco,
    # a primeira é mantida para que os erros apareçam nos próprios campos.
    itens = [
        item
        for item in linhas
        if item.produto or item.valor_unitario or item.quantidade not in ("", "1")
    ] or linhas[:1]

    return Pedido(
        cliente=campo("cliente"),
        contato=campo("contato"),
        pagamento=campo("pagamento"),
        entrega=campo("entrega"),
        observacoes=campo("observacoes"),
        itens=itens,
    )


def validar(pedido: Pedido) -> Pedido:
    """Preenche erros, subtotais, quantidade total e total do pedido."""
    erros = pedido.erros

    if not pedido.cliente:
        erros["cliente"] = "Informe o nome do cliente."

    digitos = somente_digitos(pedido.contato)
    if not pedido.contato:
        erros["contato"] = "Informe o contato."
    elif not SO_MASCARA_CONTATO.match(pedido.contato):
        erros["contato"] = "Use somente números."
    elif len(digitos) != DIGITOS_CONTATO:
        erros["contato"] = "Informe DDD e número: (00) 00000-0000."
    pedido.contato = formatar_contato(pedido.contato) if "contato" not in erros else pedido.contato

    if pedido.pagamento not in FORMAS_PAGAMENTO:
        erros["pagamento"] = "Escolha a forma de pagamento."
    if pedido.entrega not in FORMAS_ENTREGA:
        erros["entrega"] = "Escolha a forma de entrega."

    if not pedido.itens:
        erros["itens"] = "Adicione pelo menos um item."

    for item in pedido.itens:
        if not item.produto:
            item.erros["produto"] = "Informe o produto."

        quantidade = None
        if not item.quantidade:
            item.erros["quantidade"] = "Informe a quantidade."
        elif not item.quantidade.isdigit():
            item.erros["quantidade"] = "Use um número inteiro."
        elif int(item.quantidade) < 1:
            item.erros["quantidade"] = "Quantidade mínima é 1."
        else:
            quantidade = int(item.quantidade)

        valor = None
        if not item.valor_unitario:
            item.erros["valor_unitario"] = "Informe o valor unitário."
        else:
            valor = parse_moeda(item.valor_unitario)
            if valor is None:
                item.erros["valor_unitario"] = "Valor inválido."
            elif valor < 0:
                item.erros["valor_unitario"] = "Valor não pode ser negativo."
                valor = None
            else:
                valor = valor.quantize(Decimal("0.01"))
                item.valor_unitario = formatar_brl(valor)

        if quantidade is not None:
            pedido.quantidade_total += quantidade
        if quantidade is not None and valor is not None:
            item.subtotal = quantidade * valor
            pedido.total += item.subtotal

    return pedido
