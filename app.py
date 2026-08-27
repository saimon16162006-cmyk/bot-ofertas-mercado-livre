import os
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from flask import Flask, request, redirect, jsonify

app = Flask(__name__)

CLIENT_ID = os.environ.get("MELI_CLIENT_ID")
CLIENT_SECRET = os.environ.get("MELI_CLIENT_SECRET")
DATABASE_URL = os.environ.get("DATABASE_URL")

REDIRECT_URI = "https://bot-ofertas-mercado-livre-wg6f.onrender.com/callback"

AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada.")
    return psycopg2.connect(DATABASE_URL)


def create_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meli_tokens (
            id INTEGER PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


def save_tokens(data):
    expires_in = int(data.get("expires_in", 21600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO meli_tokens
            (id, access_token, refresh_token, expires_at, updated_at)
        VALUES
            (1, %s, %s, %s, NOW())
        ON CONFLICT (id)
        DO UPDATE SET
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            expires_at = EXCLUDED.expires_at,
            updated_at = NOW()
    """, (
        data["access_token"],
        data["refresh_token"],
        expires_at
    ))

    conn.commit()
    cur.close()
    conn.close()


def load_tokens():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT access_token, refresh_token, expires_at
        FROM meli_tokens
        WHERE id = 1
    """)

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def refresh_access_token(refresh_token):
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token
        },
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    save_tokens(data)

    return data["access_token"]


def get_access_token():
    tokens = load_tokens()

    if not tokens:
        return None

    access_token, refresh_token, expires_at = tokens

    now = datetime.now(timezone.utc)

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    # Renova alguns minutos antes de expirar.
    if now >= expires_at - timedelta(minutes=5):
        return refresh_access_token(refresh_token)

    return access_token


@app.route("/me")
def me():
    try:
        token = get_access_token()

        if not token:
            return jsonify({
                "erro": "Mercado Livre ainda não autorizado."
            }), 401

        response = requests.get(
            "https://api.mercadolibre.com/users/me",
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=30
        )

        return jsonify({
            "status": response.status_code,
            "resposta": response.json()
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500


@app.route("/")
def home():
    return """
    <h2>Bot de Ofertas Mercado Livre ONLINE!</h2>
    <p><a href="/login">Clique aqui para autorizar o Mercado Livre</a></p>
    <p><a href="/status">Ver status da autorização</a></p>
    """


@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "MELI_CLIENT_ID ou MELI_CLIENT_SECRET não configurados.", 500

    authorization_url = (
        f"{AUTH_URL}"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )

    return redirect(authorization_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return "Código de autorização não recebido.", 400

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        timeout=30
    )

    if not response.ok:
        return jsonify({
            "erro": "Não foi possível obter o token.",
            "status": response.status_code,
            "resposta": response.text
        }), 400

    data = response.json()

    if "access_token" not in data or "refresh_token" not in data:
        return jsonify({
            "erro": "Mercado Livre não retornou os tokens esperados.",
            "resposta": data
        }), 400

    save_tokens(data)

    return """
    <h2>Mercado Livre autorizado com sucesso! ✅</h2>
    <p>Access Token e Refresh Token foram salvos no banco Neon.</p>
    <p><a href="/status">Verificar status</a></p>
    """


@app.route("/produtos")
def produtos():
    try:
        token = get_access_token()

        if not token:
            return jsonify({
                "erro": "Mercado Livre ainda não autorizado."
            }), 401

        termo = request.args.get("q", "iphone").strip()
        if not termo:
            return jsonify({"erro": "Informe um termo de busca."}), 400

        headers = {"Authorization": f"Bearer {token}"}

        # 1) Descobre primeiro o domínio correto do termo.
        # Ex.: "iphone" -> MLB-CELLPHONES. Isso evita capas e acessórios.
        domain_response = requests.get(
            "https://api.mercadolibre.com/sites/MLB/domain_discovery/search",
            headers=headers,
            params={"q": termo, "limit": 1},
            timeout=30
        )

        domain_id = None
        if domain_response.ok:
            domain_results = domain_response.json()
            if isinstance(domain_results, list) and domain_results:
                domain_id = domain_results[0].get("domain_id")

        # Fallback especial para iPhone caso o preditor não retorne domínio.
        if not domain_id and "iphone" in termo.lower():
            domain_id = "MLB-CELLPHONES"

        # 2) Busca somente produtos do domínio identificado.
        params = {
            "site_id": "MLB",
            "status": "active",
            "q": termo,
            "limit": 50
        }
        if domain_id:
            params["domain_id"] = domain_id

        response = requests.get(
            "https://api.mercadolibre.com/products/search",
            headers=headers,
            params=params,
            timeout=30
        )

        if not response.ok:
            return jsonify({
                "erro": "Erro ao buscar produtos.",
                "status": response.status_code,
                "resposta": response.text
            }), response.status_code

        data = response.json()
        produtos_encontrados = []
        vistos = set()

        for produto in data.get("results", []):
            if len(produtos_encontrados) >= 10:
                break

            product_id = produto.get("id")
            result_domain = produto.get("domain_id")

            if not product_id or product_id in vistos:
                continue

            # Segurança extra: se descobrimos um domínio, não aceitamos outro.
            if domain_id and result_domain != domain_id:
                continue

            vistos.add(product_id)

            # 3) Obtém os anúncios reais que competem nessa página de produto.
            items_response = requests.get(
                f"https://api.mercadolibre.com/products/{product_id}/items",
                headers=headers,
                params={"limit": 100},
                timeout=30
            )

            if not items_response.ok:
                continue

            items = items_response.json().get("results", [])
            if not items:
                continue

            # Dá preferência a anúncio novo e escolhe o menor preço válido.
            candidatos = [
                item for item in items
                if item.get("item_id")
                and item.get("price") is not None
                and item.get("condition", "new") == "new"
            ]

            if not candidatos:
                candidatos = [
                    item for item in items
                    if item.get("item_id") and item.get("price") is not None
                ]

            if not candidatos:
                continue

            oferta = min(candidatos, key=lambda item: float(item.get("price")))
            item_id = oferta.get("item_id")
            preco = oferta.get("price")
            moeda = oferta.get("currency_id")

            item_response = requests.get(
                f"https://api.mercadolibre.com/items/{item_id}",
                headers=headers,
                timeout=30
            )

            item_data = item_response.json() if item_response.ok else {}
            link = item_data.get("permalink")
            imagem = item_data.get("thumbnail")

            if not link:
                link = f"https://produto.mercadolivre.com.br/MLB-{item_id.replace('MLB', '')}"

            if not imagem:
                pictures = produto.get("pictures") or []
                if pictures:
                    imagem = pictures[0].get("url") or pictures[0].get("secure_url")

            produtos_encontrados.append({
                "id": product_id,
                "item_id": item_id,
                "nome": produto.get("name"),
                "status": produto.get("status"),
                "dominio": result_domain,
                "preco": preco,
                "moeda": moeda,
                "imagem": imagem,
                "link": link
            })

        return jsonify({
            "busca": termo,
            "dominio": domain_id,
            "quantidade": len(produtos_encontrados),
            "produtos": produtos_encontrados
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500


@app.route("/debug-produto")
def debug_produto():
    try:
        token = get_access_token()

        if not token:
            return jsonify({
                "erro": "Mercado Livre ainda não autorizado."
            }), 401

        product_id = request.args.get("id", "MLB6055020")

        response = requests.get(
            f"https://api.mercadolibre.com/products/{product_id}",
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=30
        )

        return jsonify({
            "product_id": product_id,
            "status": response.status_code,
            "resposta": response.json() if response.ok else response.text
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500
@app.route("/adicionar-link", methods=["GET", "POST"])
def adicionar_link():
    if request.method == "GET":
        return """
        <h2>Adicionar link de afiliado</h2>
        <form method="POST">
            <p>Nome do produto:</p>
            <input type="text" name="product_name" required>

            <p>Link do produto:</p>
            <input type="text" name="product_url" required>

            <p>Link de afiliado:</p>
            <input type="text" name="affiliate_url" required>

            <br><br>
            <button type="submit">Salvar produto</button>
        </form>
        """

    product_name = request.form.get("product_name")
    product_url = request.form.get("product_url")
    affiliate_url = request.form.get("affiliate_url")

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO ml_affiliate_product_links (
                product_url,
                affiliate_url,
                product_name
            )
            VALUES (%s, %s, %s)
        """, (
            product_url,
            affiliate_url,
            product_name
        ))

        conn.commit()
        cur.close()
        conn.close()

        return """
        <h2>Produto salvo com sucesso! ✅</h2>
        <p><a href="/adicionar-link">Adicionar outro produto</a></p>
        <p><a href="/links">Ver links cadastrados</a></p>
        """

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500
@app.route("/links")
def links():
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, product_url, affiliate_url, product_name, created_at
            FROM ml_affiliate_product_links
            ORDER BY id ASC
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        resultado = []

        for row in rows:
            resultado.append({
                "id": row[0],
                "product_url": row[1],
                "affiliate_url": row[2],
                "product_name": row[3],
                "created_at": row[4].isoformat() if row[4] else None
            })

        return jsonify({
            "quantidade": len(resultado),
            "links": resultado
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500
    

@app.route("/oferta")
def oferta():
    try:
        termo = request.args.get("q", "iphone")

        with app.test_request_context(f"/produtos?q={termo}"):
            resposta = produtos()

        if isinstance(resposta, tuple):
            response_obj, status_code = resposta[0], resposta[1]
            if status_code >= 400:
                return resposta
        else:
            response_obj = resposta

        data = response_obj.get_json()
        produtos_lista = data.get("produtos", [])

        if not produtos_lista:
            return jsonify({
                "erro": "Nenhuma oferta encontrada.",
                "busca": termo
            }), 404

        validos = [
            p for p in produtos_lista
            if p.get("preco") is not None and p.get("link")
        ]

        if not validos:
            return jsonify({
                "erro": "Nenhuma oferta válida com preço e link encontrada.",
                "busca": termo
            }), 404

        melhor = min(validos, key=lambda p: float(p["preco"]))

        preco_num = float(melhor["preco"])
        preco_formatado = (
            f"R$ {preco_num:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        mensagem = (
            "🔥 OFERTA ENCONTRADA!\n\n"
            f"📦 {melhor.get('nome', 'Produto')}\n"
            f"💰 {preco_formatado}\n"
            f"🛒 Comprar: {melhor.get('link')}\n\n"
            "⚠️ Preço e disponibilidade podem mudar a qualquer momento."
        )

        return jsonify({
            "busca": termo,
            "produto": {
                "id": melhor.get("id"),
                "item_id": melhor.get("item_id"),
                "nome": melhor.get("nome"),
                "preco": melhor.get("preco"),
                "preco_formatado": preco_formatado,
                "imagem": melhor.get("imagem"),
                "link": melhor.get("link"),
                "status": melhor.get("status")
            },
            "mensagem": mensagem
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500


@app.route("/rotas")
def rotas():
    return jsonify({
        "rotas": sorted(
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint != "static"
        )
    })



def ensure_ml_affiliate_product_links_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_affiliate_product_links (
            item_id TEXT PRIMARY KEY,
            product_name TEXT,
            original_url TEXT NOT NULL,
            affiliate_url TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_affiliate_link(item_id):
    if not item_id:
        return None

    ensure_ml_affiliate_product_links_table()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT affiliate_url
        FROM ml_affiliate_product_links
        WHERE item_id = %s
        LIMIT 1
    """, (item_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    return row[0] if row else None


@app.route("/afiliado", methods=["GET", "POST"])
def afiliado():
    ensure_ml_affiliate_product_links_table()

    if request.method == "GET":
        item_id = request.args.get("item_id", "")
        nome = request.args.get("nome", "")
        link = request.args.get("link", "")

        return f"""
        <!doctype html>
        <html lang="pt-BR">
        <head>
            <meta charset="utf-8">
            <title>Cadastrar link de afiliado</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 760px;
                    margin: 40px auto;
                    padding: 0 16px;
                    line-height: 1.5;
                }}
                input {{
                    width: 100%;
                    padding: 10px;
                    margin: 6px 0 16px;
                    box-sizing: border-box;
                }}
                button {{
                    padding: 12px 18px;
                    cursor: pointer;
                }}
                .box {{
                    padding: 16px;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                }}
            </style>
        </head>
        <body>
            <h2>Cadastrar link de afiliado</h2>
            <div class="box">
                <form method="POST">
                    <label>Item ID</label>
                    <input name="item_id" value="{item_id}" required>

                    <label>Nome do produto</label>
                    <input name="product_name" value="{nome}">

                    <label>Link normal do Mercado Livre</label>
                    <input name="original_url" value="{link}" required>

                    <label>Link de afiliado gerado no Mercado Livre</label>
                    <input name="affiliate_url" placeholder="Cole aqui o seu link de afiliado" required>

                    <button type="submit">Salvar link</button>
                </form>
            </div>
        </body>
        </html>
        """

    item_id = request.form.get("item_id", "").strip()
    product_name = request.form.get("product_name", "").strip()
    original_url = request.form.get("original_url", "").strip()
    affiliate_url = request.form.get("affiliate_url", "").strip()

    if not item_id or not original_url or not affiliate_url:
        return jsonify({
            "erro": "item_id, original_url e affiliate_url são obrigatórios."
        }), 400

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ml_affiliate_product_links (
            item_id, product_name, original_url, affiliate_url, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (item_id)
        DO UPDATE SET
            product_name = EXCLUDED.product_name,
            original_url = EXCLUDED.original_url,
            affiliate_url = EXCLUDED.affiliate_url,
            updated_at = NOW()
    """, (item_id, product_name, original_url, affiliate_url))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "affiliate_url": affiliate_url
    })


@app.route("/oferta-afiliada")
def oferta_afiliada():
    try:
        termo = request.args.get("q", "iphone")

        with app.test_request_context(f"/produtos?q={termo}"):
            resposta = produtos()

        if isinstance(resposta, tuple):
            response_obj, status_code = resposta[0], resposta[1]
            if status_code >= 400:
                return resposta
        else:
            response_obj = resposta

        data = response_obj.get_json()
        produtos_lista = data.get("produtos", [])

        validos = [
            p for p in produtos_lista
            if p.get("preco") is not None and p.get("link") and p.get("item_id")
        ]

        if not validos:
            return jsonify({
                "erro": "Nenhuma oferta válida encontrada.",
                "busca": termo
            }), 404

        melhor = min(validos, key=lambda p: float(p["preco"]))
        item_id = melhor.get("item_id")
        affiliate_url = get_affiliate_link(item_id)

        preco_num = float(melhor["preco"])
        preco_formatado = (
            f"R$ {preco_num:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        if not affiliate_url:
            cadastro_url = (
                f"/afiliado?item_id={item_id}"
                f"&nome={melhor.get('nome','')}"
                f"&link={melhor.get('link','')}"
            )

            return jsonify({
                "status": "aguardando_link_afiliado",
                "busca": termo,
                "produto": melhor,
                "cadastro_link": cadastro_url,
                "instrucao": "Gere o link no Mercado Livre e salve nesta rota."
            }), 409

        mensagem = (
            "🔥 OFERTA ENCONTRADA!\n\n"
            f"📦 {melhor.get('nome', 'Produto')}\n"
            f"💰 {preco_formatado}\n"
            f"🛒 Comprar: {affiliate_url}\n\n"
            "⚠️ Preço e disponibilidade podem mudar a qualquer momento."
        )

        return jsonify({
            "busca": termo,
            "produto": {
                "id": melhor.get("id"),
                "item_id": item_id,
                "nome": melhor.get("nome"),
                "preco": melhor.get("preco"),
                "preco_formatado": preco_formatado,
                "imagem": melhor.get("imagem"),
                "link_normal": melhor.get("link"),
                "link_afiliado": affiliate_url,
                "status": melhor.get("status")
            },
            "mensagem": mensagem
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500

if __name__ == "__main__":
    create_table()
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
