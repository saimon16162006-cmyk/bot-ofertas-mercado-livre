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


@app.route("/status")
def status():
    try:
        token = get_access_token()

        if not token:
            return jsonify({
                "autorizado": False,
                "mensagem": "Ainda é necessário autorizar o Mercado Livre."
            })

        return jsonify({
            "autorizado": True,
            "mensagem": "Token encontrado e válido."
        })

    except Exception as e:
        return jsonify({
            "autorizado": False,
            "erro": str(e)
        }), 500
@app.route("/produtos")
def produtos():
    try:
        token = get_access_token()

        if not token:
            return jsonify({
                "erro": "Mercado Livre ainda não autorizado."
            }), 401

        termo = request.args.get("q", "iphone")

        response = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            headers={
                "Authorization": f"Bearer {token}"
            },
            params={
                "q": termo,
                "limit": 10
            },
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

        for produto in data.get("results", [])[:10]:
            produtos_encontrados.append({
                "id": produto.get("id"),
                "nome": produto.get("name"),
                "status": produto.get("status"),
                "dominio": produto.get("domain_id")
            })

        return jsonify({
            "busca": termo,
            "quantidade": len(produtos_encontrados),
            "produtos": produtos_encontrados
        })

    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500
create_table()

@app.route("/links")
def links():
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, product_url, affiliate_url, product_name, created_at
            FROM affiliate_links
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
    
if __name__ == "__main__":
create_table()
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
