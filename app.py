from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import os

DATABASE_URL = os.environ.get("DATABASE_URL")


class BancoPostgres:
    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(url, row_factory=dict_row)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "glenia-controle-chave-local")

BANCO = os.environ.get("DATABASE_PATH", "banco.db")


def conectar():
    if DATABASE_URL:
        return BancoPostgres(DATABASE_URL)

    conexao = sqlite3.connect(BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def criar_banco():
    if DATABASE_URL:
        return

    conexao = conectar()

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            nome_loja TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            categoria TEXT,
            preco_normal REAL NOT NULL DEFAULT 0,
            quantidade INTEGER NOT NULL DEFAULT 0,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (loja_id) REFERENCES usuarios(id) ON DELETE CASCADE
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            quantidade INTEGER NOT NULL,
            custo_unitario REAL NOT NULL,
            total REAL NOT NULL,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (loja_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE CASCADE
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loja_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            quantidade INTEGER NOT NULL,
            preco_unitario REAL NOT NULL,
            total REAL NOT NULL,
            forma_pagamento TEXT NOT NULL,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (loja_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE CASCADE
        )
    """)

    conexao.commit()
    conexao.close()


def usuario_logado():
    return session.get("usuario_id")


def login_obrigatorio(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not usuario_logado():
            flash("Faça login para acessar sua loja.", "erro")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def dados_usuario():
    usuario = None
    if usuario_logado():
        conexao = conectar()
        usuario = conexao.execute(
            "SELECT id, nome, nome_loja, email FROM usuarios WHERE id = ?",
            (usuario_logado(),)
        ).fetchone()
        conexao.close()
    return {"usuario_atual": usuario}


@app.template_filter("moeda")
def moeda(valor):
    return f"R$ {float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("data_br")
def data_br(valor):
    if not valor:
        return ""
    try:
        dt = datetime.fromisoformat(str(valor))
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(valor)


@app.route("/login", methods=["GET", "POST"])
def login():
    if usuario_logado():
        return redirect(url_for("inicio"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        conexao = conectar()
        usuario = conexao.execute(
            "SELECT * FROM usuarios WHERE email = ?", (email,)
        ).fetchone()
        conexao.close()

        if usuario and check_password_hash(usuario["senha_hash"], senha):
            session.clear()
            session["usuario_id"] = usuario["id"]
            return redirect(url_for("inicio"))

        flash("E-mail ou senha incorretos.", "erro")

    return render_template("login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if usuario_logado():
        return redirect(url_for("inicio"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        nome_loja = request.form.get("nome_loja", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar_senha", "")

        if not nome or not nome_loja or not email or not senha:
            flash("Preencha todos os campos.", "erro")
            return render_template("cadastro.html")

        if senha != confirmar:
            flash("As senhas não coincidem.", "erro")
            return render_template("cadastro.html")

        if len(senha) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.", "erro")
            return render_template("cadastro.html")

        conexao = conectar()
        existente = conexao.execute(
            "SELECT id FROM usuarios WHERE email = ?", (email,)
        ).fetchone()

        if existente:
            conexao.close()
            flash("Esse e-mail já está cadastrado.", "erro")
            return render_template("cadastro.html")

        cursor = conexao.execute("""
            INSERT INTO usuarios (nome, nome_loja, email, senha_hash)
            VALUES (?, ?, ?, ?)
            RETURNING id
        """, (nome, nome_loja, email, generate_password_hash(senha)))
        usuario_id = cursor.fetchone()["id"]
        conexao.commit()
        conexao.close()

        session.clear()
        session["usuario_id"] = usuario_id
        flash("Conta criada com sucesso. Bem-vinda(o)!", "sucesso")
        return redirect(url_for("inicio"))

    return render_template("cadastro.html")


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_obrigatorio
def inicio():
    loja_id = usuario_logado()
    conexao = conectar()

    total_produtos = conexao.execute(
        "SELECT COUNT(*) AS valor FROM produtos WHERE loja_id = ?", (loja_id,)
    ).fetchone()["valor"]

    estoque_total = conexao.execute(
        "SELECT COALESCE(SUM(quantidade), 0) AS valor FROM produtos WHERE loja_id = ?", (loja_id,)
    ).fetchone()["valor"]

    if DATABASE_URL:
        vendas_hoje = conexao.execute("""
            SELECT COALESCE(SUM(quantidade), 0) AS valor
            FROM vendas
            WHERE loja_id = ? AND DATE(data) = CURRENT_DATE
        """, (loja_id,)).fetchone()["valor"]

        vendas_mes = conexao.execute("""
            SELECT COALESCE(SUM(total), 0) AS valor
            FROM vendas
            WHERE loja_id = ?
              AND DATE_TRUNC('month', data) = DATE_TRUNC('month', CURRENT_TIMESTAMP)
        """, (loja_id,)).fetchone()["valor"]
    else:
        vendas_hoje = conexao.execute("""
            SELECT COALESCE(SUM(quantidade), 0) AS valor
            FROM vendas
            WHERE loja_id = ? AND DATE(data, 'localtime') = DATE('now', 'localtime')
        """, (loja_id,)).fetchone()["valor"]

        vendas_mes = conexao.execute("""
            SELECT COALESCE(SUM(total), 0) AS valor
            FROM vendas
            WHERE loja_id = ? AND strftime('%Y-%m', data, 'localtime') = strftime('%Y-%m', 'now', 'localtime')
        """, (loja_id,)).fetchone()["valor"]

    total_gasto = conexao.execute(
        "SELECT COALESCE(SUM(total), 0) AS valor FROM compras WHERE loja_id = ?", (loja_id,)
    ).fetchone()["valor"]

    total_vendas = conexao.execute(
        "SELECT COALESCE(SUM(total), 0) AS valor FROM vendas WHERE loja_id = ?", (loja_id,)
    ).fetchone()["valor"]

    resultado = total_vendas - total_gasto
    conexao.close()

    return render_template(
        "inicio.html",
        total_produtos=total_produtos,
        estoque_total=estoque_total,
        vendas_hoje=vendas_hoje,
        vendas_mes=vendas_mes,
        total_gasto=total_gasto,
        total_vendas=total_vendas,
        resultado=resultado
    )


@app.route("/produtos")
@login_obrigatorio
def produtos():
    conexao = conectar()
    lista = conexao.execute("""
        SELECT * FROM produtos
        WHERE loja_id = ?
        ORDER BY id DESC
    """, (usuario_logado(),)).fetchall()
    conexao.close()
    return render_template("produtos.html", produtos=lista)


@app.route("/cadastrar-produto", methods=["POST"])
@login_obrigatorio
def cadastrar_produto():
    nome = request.form["nome"].strip()
    categoria = request.form.get("categoria", "").strip()
    preco_normal = float(request.form["preco_normal"])
    quantidade = int(request.form.get("quantidade", 0))

    if not nome or preco_normal < 0 or quantidade < 0:
        flash("Confira os dados do produto.", "erro")
        return redirect(url_for("produtos"))

    conexao = conectar()
    cursor = conexao.execute("""
        INSERT INTO produtos (loja_id, nome, categoria, preco_normal, quantidade)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
    """, (usuario_logado(), nome, categoria, preco_normal, quantidade))
    produto_id = cursor.fetchone()["id"]

    custo_inicial = request.form.get("custo_inicial", "").strip()
    if quantidade > 0 and custo_inicial:
        custo = float(custo_inicial)
        if custo >= 0:
            conexao.execute("""
                INSERT INTO compras
                (loja_id, produto_id, quantidade, custo_unitario, total)
                VALUES (?, ?, ?, ?, ?)
            """, (usuario_logado(), produto_id, quantidade, custo, quantidade * custo))

    conexao.commit()
    conexao.close()

    flash("Produto cadastrado com sucesso.", "sucesso")
    return redirect(url_for("produtos"))


@app.route("/produto/<int:id>/editar", methods=["GET", "POST"])
@login_obrigatorio
def editar_produto(id):
    conexao = conectar()

    if request.method == "POST":
        nome = request.form["nome"].strip()
        categoria = request.form.get("categoria", "").strip()
        preco_normal = float(request.form["preco_normal"])

        conexao.execute("""
            UPDATE produtos
            SET nome = ?, categoria = ?, preco_normal = ?
            WHERE id = ? AND loja_id = ?
        """, (nome, categoria, preco_normal, id, usuario_logado()))
        conexao.commit()
        conexao.close()

        flash("Produto atualizado.", "sucesso")
        return redirect(url_for("produtos"))

    produto = conexao.execute(
        "SELECT * FROM produtos WHERE id = ? AND loja_id = ?", (id, usuario_logado())
    ).fetchone()
    conexao.close()

    if produto is None:
        return "Produto não encontrado.", 404

    return render_template("editar_produto.html", produto=produto)


@app.route("/excluir-produto/<int:id>", methods=["POST"])
@login_obrigatorio
def excluir_produto(id):
    conexao = conectar()

    produto = conexao.execute(
        "SELECT id FROM produtos WHERE id = ? AND loja_id = ?", (id, usuario_logado())
    ).fetchone()
    if produto is None:
        conexao.close()
        return "Produto não encontrado.", 404

    movimentacoes = conexao.execute("""
        SELECT
            (SELECT COUNT(*) FROM vendas WHERE produto_id = ? AND loja_id = ?) +
            (SELECT COUNT(*) FROM compras WHERE produto_id = ? AND loja_id = ?) AS valor
    """, (id, usuario_logado(), id, usuario_logado())).fetchone()["valor"]

    if movimentacoes > 0:
        conexao.close()
        flash("Esse produto já possui movimentações e não pode ser excluído para preservar o histórico.", "erro")
        return redirect(url_for("produtos"))

    conexao.execute("DELETE FROM produtos WHERE id = ? AND loja_id = ?", (id, usuario_logado()))
    conexao.commit()
    conexao.close()

    flash("Produto excluído.", "sucesso")
    return redirect(url_for("produtos"))


@app.route("/compras")
@login_obrigatorio
def compras():
    conexao = conectar()
    produtos = conexao.execute(
        "SELECT * FROM produtos WHERE loja_id = ? ORDER BY nome", (usuario_logado(),)
    ).fetchall()
    registros = conexao.execute("""
        SELECT compras.*, produtos.nome
        FROM compras
        JOIN produtos ON produtos.id = compras.produto_id
        WHERE compras.loja_id = ?
        ORDER BY compras.id DESC
    """, (usuario_logado(),)).fetchall()
    conexao.close()

    return render_template("compras.html", produtos=produtos, compras=registros)


@app.route("/registrar-compra", methods=["POST"])
@login_obrigatorio
def registrar_compra():
    produto_id = int(request.form["produto_id"])
    quantidade = int(request.form["quantidade"])
    custo = float(request.form["custo_unitario"])

    if quantidade <= 0 or custo < 0:
        flash("Informe uma quantidade e um custo válidos.", "erro")
        return redirect(url_for("compras"))

    conexao = conectar()
    produto = conexao.execute(
        "SELECT id FROM produtos WHERE id = ? AND loja_id = ?", (produto_id, usuario_logado())
    ).fetchone()

    if produto is None:
        conexao.close()
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("compras"))

    total = quantidade * custo

    conexao.execute("""
        INSERT INTO compras
        (loja_id, produto_id, quantidade, custo_unitario, total)
        VALUES (?, ?, ?, ?, ?)
    """, (usuario_logado(), produto_id, quantidade, custo, total))

    conexao.execute("""
        UPDATE produtos
        SET quantidade = quantidade + ?
        WHERE id = ? AND loja_id = ?
    """, (quantidade, produto_id, usuario_logado()))

    conexao.commit()
    conexao.close()

    flash("Entrada de produtos registrada.", "sucesso")
    return redirect(url_for("compras"))


@app.route("/vendas")
@login_obrigatorio
def vendas():
    conexao = conectar()
    produtos = conexao.execute("""
        SELECT * FROM produtos
        WHERE loja_id = ? AND quantidade > 0
        ORDER BY nome
    """, (usuario_logado(),)).fetchall()
    conexao.close()
    return render_template("vendas.html", produtos=produtos)


@app.route("/registrar-venda", methods=["POST"])
@login_obrigatorio
def registrar_venda():
    produto_id = int(request.form["produto_id"])
    quantidade = int(request.form["quantidade"])
    preco_normal = float(request.form["preco_normal"])
    preco_promocional = request.form.get("preco_promocional", "").strip()
    forma_pagamento = request.form["forma_pagamento"]

    if quantidade <= 0:
        flash("A quantidade deve ser maior que zero.", "erro")
        return redirect(url_for("vendas"))

    preco = float(preco_promocional) if preco_promocional else preco_normal

    if preco < 0:
        flash("Preço inválido.", "erro")
        return redirect(url_for("vendas"))

    conexao = conectar()
    produto = conexao.execute(
        "SELECT * FROM produtos WHERE id = ? AND loja_id = ?", (produto_id, usuario_logado())
    ).fetchone()

    if produto is None:
        conexao.close()
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("vendas"))

    if quantidade > produto["quantidade"]:
        conexao.close()
        flash(f"Estoque insuficiente. Disponível: {produto['quantidade']}.", "erro")
        return redirect(url_for("vendas"))

    total = quantidade * preco

    conexao.execute("""
        INSERT INTO vendas
        (loja_id, produto_id, quantidade, preco_unitario, total, forma_pagamento)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (usuario_logado(), produto_id, quantidade, preco, total, forma_pagamento))

    conexao.execute("""
        UPDATE produtos
        SET quantidade = quantidade - ?
        WHERE id = ? AND loja_id = ?
    """, (quantidade, produto_id, usuario_logado()))

    conexao.commit()
    conexao.close()

    flash("Venda registrada e estoque atualizado.", "sucesso")
    return redirect(url_for("historico"))


@app.route("/historico")
@login_obrigatorio
def historico():
    conexao = conectar()
    vendas = conexao.execute("""
        SELECT vendas.*, produtos.nome
        FROM vendas
        JOIN produtos ON produtos.id = vendas.produto_id
        WHERE vendas.loja_id = ?
        ORDER BY vendas.id DESC
    """, (usuario_logado(),)).fetchall()
    conexao.close()
    return render_template("historico.html", vendas=vendas)


@app.route("/historico-compras")
@login_obrigatorio
def historico_compras():
    conexao = conectar()
    compras = conexao.execute("""
        SELECT compras.*, produtos.nome
        FROM compras
        JOIN produtos ON produtos.id = compras.produto_id
        WHERE compras.loja_id = ?
        ORDER BY compras.id DESC
    """, (usuario_logado(),)).fetchall()
    conexao.close()
    return render_template("historico_compras.html", compras=compras)


if __name__ == "__main__":
    criar_banco()
    app.run(debug=True)
else:
    criar_banco()
