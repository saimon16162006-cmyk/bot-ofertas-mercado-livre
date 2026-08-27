import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

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
    <h2>Bot de Ofertas Mercado Livre ONLINE! ✅</h2>
    <p><a href="/login">Autorizar o Mercado Livre</a></p>
    <p><a href="/status">Ver status da autorização</a></p>
    <p><a href="/produtos?q=iphone">Testar busca de produtos</a></p>
    <p><a href="/oferta?q=iphone">Testar melhor oferta</a></p>
    <p><a href="/configurar-afiliado">Configurar afiliado</a></p>
    <p><a href="/oferta-afiliada?q=iphone">Testar oferta afiliada</a></p>
    <p><a href="/rotas">Ver rotas disponíveis</a></p>
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


@app.route("/status")
def status():
    """Verifica se a autorização do Mercado Livre está válida sem expor os tokens."""
    try:
        tokens = load_tokens()

        if not tokens:
            return """
            <h2>Mercado Livre ainda não autorizado. ❌</h2>
            <p><a href="/login">Autorizar agora</a></p>
            <p><a href="/">Voltar para o início</a></p>
            """, 401

        token = get_access_token()

        if not token:
            return """
            <h2>Não foi possível obter um Access Token válido. ❌</h2>
            <p><a href="/login">Autorizar novamente</a></p>
            <p><a href="/">Voltar para o início</a></p>
            """, 401

        response = requests.get(
            "https://api.mercadolibre.com/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )

        if not response.ok:
            return f"""
            <h2>Autorização encontrada, mas o Mercado Livre recusou o token. ⚠️</h2>
            <p>Status da API: {response.status_code}</p>
            <p><a href="/login">Autorizar novamente</a></p>
            <p><a href="/">Voltar para o início</a></p>
            """, response.status_code

        user_data = response.json()
        nickname = user_data.get("nickname") or user_data.get("first_name") or "Conta Mercado Livre"
        user_id = user_data.get("id", "-")

        return f"""
        <h2>Mercado Livre conectado com sucesso! ✅</h2>
        <p><strong>Conta:</strong> {nickname}</p>
        <p><strong>ID:</strong> {user_id}</p>
        <p>Access Token válido e Refresh Token salvo no banco Neon.</p>

        <h3>Testes</h3>
        <p><a href="/produtos?q=iphone">Testar busca de produtos</a></p>
        <p><a href="/oferta?q=iphone">Testar melhor oferta</a></p>
        <p><a href="/configurar-afiliado">Configurar afiliado</a></p>
        <p><a href="/oferta-afiliada?q=iphone">Testar oferta afiliada</a></p>
        <p><a href="/rotas">Ver rotas do bot</a></p>
        <p><a href="/">Voltar para o início</a></p>
        """

    except Exception as e:
        return jsonify({
            "erro": "Falha ao verificar o status da autorização.",
            "detalhes": str(e)
        }), 500


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



def ensure_affiliate_batch_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_affiliate_batch (
            position INTEGER PRIMARY KEY,
            item_id TEXT,
            product_name TEXT,
            original_url TEXT NOT NULL,
            affiliate_url TEXT,
            price NUMERIC,
            image_url TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def clear_affiliate_batch():
    ensure_affiliate_batch_table()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ml_affiliate_batch")
    conn.commit()
    cur.close()
    conn.close()


def save_affiliate_batch(products):
    ensure_affiliate_batch_table()
    clear_affiliate_batch()

    conn = get_connection()
    cur = conn.cursor()

    for pos, p in enumerate(products, start=1):
        cur.execute("""
            INSERT INTO ml_affiliate_batch (
                position, item_id, product_name, original_url,
                affiliate_url, price, image_url, updated_at
            )
            VALUES (%s, %s, %s, %s, NULL, %s, %s, NOW())
        """, (
            pos,
            p.get("item_id"),
            p.get("nome"),
            p.get("link"),
            p.get("preco"),
            p.get("imagem")
        ))

    conn.commit()
    cur.close()
    conn.close()


def load_affiliate_batch():
    ensure_affiliate_batch_table()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT position, item_id, product_name, original_url,
               affiliate_url, price, image_url
        FROM ml_affiliate_batch
        ORDER BY position ASC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def format_brl(value):
    value = float(value)
    return (
        f"R$ {value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


@app.route("/lote-afiliado")
def lote_afiliado():
    try:
        termo = request.args.get("q", "iphone").strip()

        with app.test_request_context(f"/produtos?q={termo}"):
            resposta = produtos()

        if isinstance(resposta, tuple):
            response_obj, status_code = resposta[0], resposta[1]
            if status_code >= 400:
                return resposta
        else:
            response_obj = resposta

        data = response_obj.get_json()
        encontrados = [
            p for p in data.get("produtos", [])
            if p.get("item_id") and p.get("link") and p.get("preco") is not None
        ]

        if not encontrados:
            return jsonify({"erro": "Nenhum produto válido encontrado."}), 404

        encontrados = sorted(encontrados, key=lambda p: float(p["preco"]))[:10]
        save_affiliate_batch(encontrados)

        links = "\n".join(p["link"] for p in encontrados)

        linhas = []
        for i, p in enumerate(encontrados, start=1):
            linhas.append(
                f"<tr><td>{i}</td><td>{p.get('nome','')}</td>"
                f"<td>{format_brl(p.get('preco'))}</td></tr>"
            )

        return f"""
        <!doctype html>
        <html lang="pt-BR">
        <head>
            <meta charset="utf-8">
            <title>Lote de Afiliados</title>
            <style>
                body {{ font-family: Arial; max-width: 980px; margin: 35px auto; padding: 0 16px; }}
                textarea {{ width: 100%; min-height: 260px; padding: 12px; box-sizing: border-box; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                button {{ padding: 12px 16px; cursor: pointer; }}
            </style>
        </head>
        <body>
            <h2>Lote pronto para gerar links de afiliado ✅</h2>
            <p>Copie os links abaixo e cole no Gerador de produtos recomendados do Mercado Livre.</p>

            <textarea id="links" readonly>{links}</textarea>
            <p>
                <button onclick="navigator.clipboard.writeText(document.getElementById('links').value)">
                    Copiar todos os links
                </button>
            </p>

            <p><a href="/importar-links-afiliados">Depois clique aqui para colar os links afiliados</a></p>

            <table>
                <tr><th>#</th><th>Produto</th><th>Preço</th></tr>
                {''.join(linhas)}
            </table>
        </body>
        </html>
        """

    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/importar-links-afiliados", methods=["GET", "POST"])
def importar_links_afiliados():
    try:
        rows = load_affiliate_batch()

        if request.method == "GET":
            if not rows:
                return """
                <h2>Nenhum lote preparado.</h2>
                <p><a href="/lote-afiliado?q=iphone">Preparar lote</a></p>
                """

            lista = "".join(
                f"<li>{r[2]} — {format_brl(r[5])}</li>"
                for r in rows
            )

            return f"""
            <!doctype html>
            <html lang="pt-BR">
            <head><meta charset="utf-8"><title>Importar links afiliados</title></head>
            <body style="font-family:Arial;max-width:900px;margin:35px auto;padding:0 16px;">
                <h2>Colar links oficiais de afiliado</h2>
                <p>Cole exatamente um link por linha, na mesma ordem.</p>
                <ol>{lista}</ol>

                <form method="POST">
                    <textarea name="affiliate_links" required
                        style="width:100%;min-height:260px;padding:12px;box-sizing:border-box;"></textarea>
                    <br><br>
                    <button type="submit" style="padding:12px 18px;">Salvar links</button>
                </form>
            </body>
            </html>
            """

        raw = request.form.get("affiliate_links", "")
        links = [x.strip() for x in raw.splitlines() if x.strip()]

        if len(links) != len(rows):
            return (
                f"<h2>Quantidade incorreta ❌</h2>"
                f"<p>O lote tem {len(rows)} produtos, mas você colou {len(links)} links.</p>"
                f"<p><a href='/importar-links-afiliados'>Voltar</a></p>",
                400
            )

        conn = get_connection()
        cur = conn.cursor()

        for row, affiliate_url in zip(rows, links):
            position = row[0]
            cur.execute("""
                UPDATE ml_affiliate_batch
                SET affiliate_url = %s, updated_at = NOW()
                WHERE position = %s
            """, (affiliate_url, position))

        conn.commit()
        cur.close()
        conn.close()

        return """
        <h2>Links salvos com sucesso! ✅</h2>
        <p><a href="/ofertas-prontas">Ver ofertas prontas</a></p>
        """

    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/ofertas-prontas")
def ofertas_prontas():
    try:
        rows = load_affiliate_batch()

        if not rows:
            return jsonify({"erro": "Nenhum lote preparado."}), 404

        faltando = [r for r in rows if not r[4]]
        if faltando:
            return jsonify({
                "status": "links_afiliados_faltando",
                "quantidade_faltando": len(faltando),
                "importar": "/importar-links-afiliados"
            }), 409

        ofertas = []
        for r in rows:
            position, item_id, product_name, original_url, affiliate_url, price, image_url = r
            preco_formatado = format_brl(price)

            mensagem = (
                "🔥 OFERTA ENCONTRADA!\n\n"
                f"📦 {product_name}\n"
                f"💰 {preco_formatado}\n"
                f"🛒 Comprar: {affiliate_url}\n\n"
                "⚠️ Preço e disponibilidade podem mudar a qualquer momento."
            )

            ofertas.append({
                "item_id": item_id,
                "nome": product_name,
                "preco": float(price),
                "preco_formatado": preco_formatado,
                "imagem": image_url,
                "link_normal": original_url,
                "link_afiliado": affiliate_url,
                "mensagem": mensagem
            })

        return jsonify({
            "quantidade": len(ofertas),
            "ofertas": ofertas
        })

    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/imagem-proxy")
def imagem_proxy():
    try:
        image_url = request.args.get("url", "").strip()
        if not image_url:
            return "URL da imagem não informada.", 400

        if not (
            image_url.startswith("https://http2.mlstatic.com/")
            or image_url.startswith("https://http2.mlstatic.com")
        ):
            return "Domínio de imagem não permitido.", 400

        r = requests.get(image_url, timeout=30)
        if not r.ok:
            return "Não foi possível carregar a imagem.", r.status_code

        content_type = r.headers.get("Content-Type", "image/jpeg")

        from flask import Response
        return Response(
            r.content,
            status=200,
            content_type=content_type,
            headers={
                "Cache-Control": "public, max-age=3600"
            }
        )

    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/painel-ofertas")
def painel_ofertas():
    try:
        from urllib.parse import quote

        rows = load_affiliate_batch()

        if not rows:
            return """
            <h2>Nenhum lote preparado.</h2>
            <p><a href="/lote-afiliado?q=iphone">Preparar lote</a></p>
            """, 404

        faltando = [r for r in rows if not r[4]]
        if faltando:
            return """
            <h2>Ainda faltam links de afiliado.</h2>
            <p><a href="/importar-links-afiliados">Importar links afiliados</a></p>
            """, 409

        cards = []

        for r in rows:
            position, item_id, product_name, original_url, affiliate_url, price, image_url = r
            preco_formatado = format_brl(price)

            mensagem = (
                "🔥 OFERTA ENCONTRADA!\n\n"
                f"📦 {product_name}\n"
                f"💰 {preco_formatado}\n\n"
                "🛒 PEGUE A OFERTA AQUI 👇\n"
                f"🔗 {affiliate_url}\n\n"
                "⚠️ Preço e disponibilidade podem mudar a qualquer momento."
            )

            imagem = image_url or ""
            imagem_proxy = "/imagem-proxy?url=" + quote(imagem, safe="") if imagem else ""

            card = f"""
            <div class="card" id="card-{position}" data-item="{item_id}">
                <div class="imgbox">
                    <img src="{imagem}" alt="{product_name}">
                </div>

                <div class="content">
                    <div class="card-top">
                        <div>
                            <h3>{product_name}</h3>
                            <div class="price">{preco_formatado}</div>
                        </div>
                        <span class="badge" id="badge-{position}">Pendente</span>
                    </div>

                    <textarea id="msg-{position}" readonly>{mensagem}</textarea>

                    <div class="actions">
                        <button class="share"
                            onclick="shareOffer('{imagem_proxy}', 'msg-{position}', '{product_name}', {position})">
                            📤 Compartilhar foto + mensagem
                        </button>

                        <button onclick="copyText('msg-{position}')">
                            Copiar mensagem
                        </button>

                        <button class="whatsapp" onclick="sendWhatsApp('msg-{position}', {position})">
                            Enviar só texto no WhatsApp
                        </button>

                        <button class="done" onclick="markSent({position}, '{item_id}')">
                            Marcar como enviada
                        </button>

                        <a class="btn" href="{affiliate_url}" target="_blank">
                            Abrir link afiliado
                        </a>

                        <a class="btn" href="{imagem}" target="_blank">
                            Abrir imagem
                        </a>
                    </div>

                    <p class="hint">
                        <strong>Para ficar igual ao exemplo:</strong>
                        use “Compartilhar foto + mensagem”. No celular, escolha o WhatsApp e depois o grupo.
                        Se o navegador não permitir compartilhar a foto, o painel usa o modo alternativo.
                    </p>
                </div>
            </div>
            """
            cards.append(card)

        html = f"""
        <!doctype html>
        <html lang="pt-BR">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Painel de Ofertas</title>
            <style>
                * {{ box-sizing: border-box; }}
                body {{
                    font-family: Arial, sans-serif;
                    margin: 0;
                    background: #f5f5f5;
                    color: #222;
                }}
                .wrap {{
                    max-width: 1100px;
                    margin: 30px auto;
                    padding: 0 16px 40px;
                }}
                h1 {{ margin-bottom: 8px; }}
                .sub {{ color: #555; margin-top: 0; }}
                .toolbar {{
                    background: #fff;
                    border: 1px solid #ddd;
                    border-radius: 12px;
                    padding: 14px;
                    margin: 18px 0;
                }}
                .toolbar-row {{
                    display: flex;
                    gap: 10px;
                    flex-wrap: wrap;
                    align-items: center;
                }}
                .toolbar input {{
                    min-width: 260px;
                    padding: 11px;
                    border: 1px solid #ccc;
                    border-radius: 8px;
                }}
                .counter {{
                    font-weight: bold;
                    margin-left: auto;
                }}
                .card {{
                    display: flex;
                    gap: 22px;
                    background: #fff;
                    border: 1px solid #ddd;
                    border-radius: 14px;
                    padding: 18px;
                    margin: 18px 0;
                }}
                .card.sent {{ opacity: .55; }}
                .card.sent .badge {{
                    background: #d9f6df;
                    color: #176b2c;
                }}
                .imgbox {{
                    width: 230px;
                    min-width: 230px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .imgbox img {{
                    width: 220px;
                    height: 220px;
                    object-fit: contain;
                    border-radius: 10px;
                    background: #fff;
                }}
                .content {{ flex: 1; }}
                .card-top {{
                    display: flex;
                    justify-content: space-between;
                    gap: 12px;
                    align-items: flex-start;
                }}
                .badge {{
                    display: inline-block;
                    padding: 6px 10px;
                    border-radius: 999px;
                    background: #fff0c9;
                    color: #805d00;
                    font-size: 12px;
                    font-weight: bold;
                }}
                .price {{
                    font-size: 26px;
                    font-weight: bold;
                    margin: 8px 0 14px;
                }}
                textarea {{
                    width: 100%;
                    min-height: 170px;
                    padding: 12px;
                    resize: vertical;
                    border: 1px solid #ccc;
                    border-radius: 8px;
                    font-family: Arial, sans-serif;
                    font-size: 15px;
                }}
                .actions {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                    margin-top: 12px;
                }}
                button, .btn {{
                    border: 0;
                    border-radius: 8px;
                    padding: 11px 15px;
                    background: #222;
                    color: #fff;
                    cursor: pointer;
                    text-decoration: none;
                    font-size: 14px;
                    display: inline-block;
                }}
                .share {{ background: #6b46c1; }}
                .whatsapp {{ background: #128C7E; }}
                .done {{ background: #4d6b50; }}
                .secondary {{ background: #555; }}
                .hint {{
                    margin-top: 12px;
                    font-size: 13px;
                    color: #555;
                    line-height: 1.45;
                }}
                @media (max-width: 720px) {{
                    .card {{ flex-direction: column; }}
                    .imgbox {{ width: 100%; min-width: 0; }}
                    .counter {{ margin-left: 0; width: 100%; }}
                }}
            </style>
            <script>
                function storageKey(itemId) {{
                    return "oferta_enviada_" + itemId;
                }}

                async function copyText(id) {{
                    const el = document.getElementById(id);
                    try {{
                        await navigator.clipboard.writeText(el.value);
                        alert("Mensagem copiada!");
                    }} catch (e) {{
                        el.select();
                        document.execCommand("copy");
                        alert("Mensagem copiada!");
                    }}
                }}

                async function shareOffer(imageProxyUrl, messageId, productName, position) {{
                    const text = document.getElementById(messageId).value;

                    try {{
                        if (!navigator.share) {{
                            throw new Error("Compartilhamento de arquivos não disponível neste navegador.");
                        }}

                        const response = await fetch(imageProxyUrl);
                        if (!response.ok) {{
                            throw new Error("Não consegui carregar a imagem.");
                        }}

                        const blob = await response.blob();

                        let extension = "jpg";
                        if (blob.type.includes("png")) extension = "png";
                        if (blob.type.includes("webp")) extension = "webp";

                        const safeName = productName
                            .replace(/[^a-zA-Z0-9À-ÿ _-]/g, "")
                            .trim()
                            .replace(/\s+/g, "_")
                            .slice(0, 60);

                        const file = new File(
                            [blob],
                            (safeName || "oferta") + "." + extension,
                            {{ type: blob.type || "image/jpeg" }}
                        );

                        const shareData = {{
                            files: [file],
                            text: text
                        }};

                        if (navigator.canShare && !navigator.canShare({{ files: [file] }})) {{
                            throw new Error("Este aparelho não permite compartilhar arquivo pelo navegador.");
                        }}

                        await navigator.share(shareData);
                        markSent(position, document.getElementById("card-" + position).dataset.item);

                    }} catch (e) {{
                        await fallbackShare(imageProxyUrl, text);
                    }}
                }}

                async function fallbackShare(imageProxyUrl, text) {{
                    try {{
                        await navigator.clipboard.writeText(text);
                    }} catch (e) {{}}

                    alert(
                        "Seu navegador não conseguiu enviar foto + legenda em um único clique.\\n\\n" +
                        "A mensagem já foi copiada. Vou abrir a imagem.\\n" +
                        "No WhatsApp: envie a imagem e cole a mensagem como legenda."
                    );

                    window.open(imageProxyUrl, "_blank");
                }}

                function sendWhatsApp(id, position) {{
                    const text = document.getElementById(id).value;
                    const url = "https://wa.me/?text=" + encodeURIComponent(text);
                    window.open(url, "_blank");
                }}

                function markSent(position, itemId) {{
                    localStorage.setItem(storageKey(itemId), "1");

                    const card = document.getElementById("card-" + position);
                    const badge = document.getElementById("badge-" + position);

                    if (card) card.classList.add("sent");
                    if (badge) badge.textContent = "Enviada ✓";

                    updateCounter();
                    goNextPending(position);
                }}

                function goNextPending(afterPosition) {{
                    const cards = Array.from(document.querySelectorAll(".card"));
                    const next = cards.find(c => {{
                        const pos = parseInt(c.id.replace("card-", ""));
                        return pos > afterPosition && !c.classList.contains("sent");
                    }});

                    if (next) {{
                        setTimeout(() => {{
                            next.scrollIntoView({{ behavior: "smooth", block: "center" }});
                        }}, 150);
                    }}
                }}

                function nextPending() {{
                    const next = Array.from(document.querySelectorAll(".card"))
                        .find(c => !c.classList.contains("sent"));

                    if (next) {{
                        next.scrollIntoView({{ behavior: "smooth", block: "center" }});
                    }} else {{
                        alert("Todas as ofertas deste lote foram marcadas como enviadas.");
                    }}
                }}

                function resetSent() {{
                    if (!confirm("Limpar as marcações de ofertas enviadas deste painel?")) return;

                    document.querySelectorAll(".card").forEach(card => {{
                        const itemId = card.dataset.item;
                        localStorage.removeItem(storageKey(itemId));
                        card.classList.remove("sent");

                        const pos = card.id.replace("card-", "");
                        const badge = document.getElementById("badge-" + pos);
                        if (badge) badge.textContent = "Pendente";
                    }});

                    updateCounter();
                }}

                function updateCounter() {{
                    const cards = Array.from(document.querySelectorAll(".card"));
                    const sent = cards.filter(c => c.classList.contains("sent")).length;
                    document.getElementById("counter").textContent =
                        sent + " de " + cards.length + " enviadas";
                }}

                function restoreSentStatus() {{
                    document.querySelectorAll(".card").forEach(card => {{
                        const itemId = card.dataset.item;

                        if (localStorage.getItem(storageKey(itemId)) === "1") {{
                            card.classList.add("sent");
                            const pos = card.id.replace("card-", "");
                            const badge = document.getElementById("badge-" + pos);
                            if (badge) badge.textContent = "Enviada ✓";
                        }}
                    }});

                    updateCounter();
                }}

                function generateNewBatch() {{
                    const term = document.getElementById("searchTerm").value.trim();
                    if (!term) return;
                    window.location.href = "/lote-afiliado?q=" + encodeURIComponent(term);
                }}

                window.addEventListener("DOMContentLoaded", restoreSentStatus);
            </script>
        </head>
        <body>
            <div class="wrap">
                <h1>Painel de Ofertas ✅</h1>
                <p class="sub">
                    Agora o botão roxo tenta compartilhar <strong>a foto do produto junto com a mensagem</strong>,
                    para ficar no formato de foto + legenda no WhatsApp.
                </p>

                <div class="toolbar">
                    <div class="toolbar-row">
                        <input id="searchTerm" value="iphone" placeholder="Ex.: air fryer, tv, notebook">
                        <button onclick="generateNewBatch()">Gerar novo lote</button>
                        <button class="secondary" onclick="nextPending()">Ir para próxima pendente</button>
                        <button class="secondary" onclick="resetSent()">Limpar marcações</button>
                        <a class="btn" href="/ofertas-prontas">Ver JSON</a>
                        <a class="btn" href="/">Início</a>
                        <span class="counter" id="counter">0 de {len(rows)} enviadas</span>
                    </div>
                </div>

                {''.join(cards)}
            </div>
        </body>
        </html>
        """

        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/rotas")
def rotas():
    return jsonify({
        "rotas": sorted(
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint != "static"
        )
    })




def ensure_affiliate_tracking_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_affiliate_tracking (
            id INTEGER PRIMARY KEY,
            tracking_params TEXT NOT NULL,
            example_url TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def extract_affiliate_tracking_params(affiliate_url):
    """
    Extrai parâmetros de rastreamento de um link OFICIAL de afiliado.
    Mantemos somente parâmetros conhecidos de tracking para não copiar lixo
    de navegação para os próximos produtos.
    """
    parsed = urlparse(affiliate_url.strip())
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    allowed = {
        "matt_tool",
        "matt_word",
        "matt_source",
        "matt_campaign",
        "matt_ad_group",
        "matt_match_type",
        "matt_network",
        "matt_device",
        "matt_creative",
        "matt_keyword",
        "matt_ad_position",
        "matt_ad_type",
        "matt_merchant_id",
        "matt_product_id",
        "matt_product_partition_id",
        "matt_target_id",
        "matt_adid",
        "matt_product_country",
        "matt_product_language",
        "tag",
    }

    clean = {k: v for k, v in params.items() if k in allowed and v}

    # Os links mais comuns do programa usam matt_tool/matt_word.
    if not clean:
        raise ValueError(
            "Não encontrei parâmetros de afiliado no link. "
            "Gere um link pelo Portal/Barra de Afiliados do Mercado Livre e cole aqui."
        )

    return clean


def save_affiliate_tracking(example_url, params):
    ensure_affiliate_tracking_table()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ml_affiliate_tracking (id, tracking_params, example_url, updated_at)
        VALUES (1, %s, %s, NOW())
        ON CONFLICT (id)
        DO UPDATE SET
            tracking_params = EXCLUDED.tracking_params,
            example_url = EXCLUDED.example_url,
            updated_at = NOW()
    """, (urlencode(params), example_url))
    conn.commit()
    cur.close()
    conn.close()


def load_affiliate_tracking():
    ensure_affiliate_tracking_table()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tracking_params, example_url
        FROM ml_affiliate_tracking
        WHERE id = 1
        LIMIT 1
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None, None

    return dict(parse_qsl(row[0], keep_blank_values=True)), row[1]


def build_affiliate_url(product_url):
    """
    Reaplica os parâmetros de tracking salvos a um link normal de produto.
    Isso elimina o cadastro manual produto por produto.
    """
    params, _ = load_affiliate_tracking()
    if not params:
        return None

    parsed = urlparse(product_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(query),
        parsed.fragment,
    ))


@app.route("/configurar-afiliado", methods=["GET", "POST"])
def configurar_afiliado():
    if request.method == "GET":
        params, example_url = load_affiliate_tracking()

        status_html = ""
        if params:
            status_html = (
                "<p style='color:green'><strong>Configuração salva ✅</strong></p>"
                f"<p>Parâmetros encontrados: {', '.join(sorted(params.keys()))}</p>"
            )

        return f"""
        <!doctype html>
        <html lang="pt-BR">
        <head>
            <meta charset="utf-8">
            <title>Configurar afiliado</title>
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
                    padding: 11px;
                    box-sizing: border-box;
                    margin: 8px 0 16px;
                }}
                button {{
                    padding: 12px 18px;
                    cursor: pointer;
                }}
                .box {{
                    border: 1px solid #ddd;
                    border-radius: 10px;
                    padding: 18px;
                }}
            </style>
        </head>
        <body>
            <h2>Configurar link de afiliado</h2>
            {status_html}
            <div class="box">
                <p>
                    Gere <strong>uma única vez</strong> um link de afiliado oficial
                    no Portal/Barra de Afiliados do Mercado Livre e cole abaixo.
                    O bot salvará apenas os parâmetros de rastreamento para reutilizar
                    nas próximas ofertas.
                </p>
                <form method="POST">
                    <label>Link oficial de afiliado:</label>
                    <input
                        type="url"
                        name="affiliate_url"
                        placeholder="https://produto.mercadolivre.com.br/..."
                        value="{example_url or ''}"
                        required
                    >
                    <button type="submit">Salvar configuração</button>
                </form>
            </div>
            <p><a href="/">Voltar para o início</a></p>
        </body>
        </html>
        """

    affiliate_url = request.form.get("affiliate_url", "").strip()

    try:
        params = extract_affiliate_tracking_params(affiliate_url)
        save_affiliate_tracking(affiliate_url, params)

        return f"""
        <h2>Afiliado configurado com sucesso! ✅</h2>
        <p>O bot encontrou e salvou: <strong>{', '.join(sorted(params.keys()))}</strong></p>
        <p>Agora não é mais necessário cadastrar link produto por produto.</p>
        <p><a href="/oferta-afiliada?q=iphone">Testar oferta afiliada</a></p>
        <p><a href="/">Voltar ao início</a></p>
        """
    except Exception as e:
        return f"""
        <h2>Não consegui reconhecer esse link. ❌</h2>
        <p>{str(e)}</p>
        <p>Gere o link pelo Portal/Barra de Afiliados e tente novamente.</p>
        <p><a href="/configurar-afiliado">Voltar</a></p>
        """, 400


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
        termo = request.args.get("q", "iphone").strip()

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
        affiliate_url = build_affiliate_url(melhor.get("link"))

        if not affiliate_url:
            return jsonify({
                "status": "configuracao_afiliado_necessaria",
                "busca": termo,
                "produto": melhor,
                "configurar": "/configurar-afiliado",
                "instrucao": (
                    "Gere apenas UM link oficial no Portal/Barra de Afiliados, "
                    "cole em /configurar-afiliado e depois teste esta rota novamente."
                )
            }), 409

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
            f"🛒 Comprar: {affiliate_url}\n\n"
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
