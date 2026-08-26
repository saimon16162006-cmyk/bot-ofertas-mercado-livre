import os
import requests
from flask import Flask, request, redirect

app = Flask(__name__)

CLIENT_ID = os.environ.get("MELI_CLIENT_ID")
CLIENT_SECRET = os.environ.get("MELI_CLIENT_SECRET")

REDIRECT_URI = "https://bot-ofertas-mercado-livre-wg6f.onrender.com/callback"


@app.route("/")
def home():
    return """
    <h2>Bot de Ofertas Mercado Livre ONLINE!</h2>
    <p><a href="/login">Clique aqui para autorizar o Mercado Livre</a></p>
    """


@app.route("/login")
def login():
    authorization_url = (
        "https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )
    return redirect(authorization_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return "Código de autorização não recebido."

    token_url = "https://api.mercadolibre.com/oauth/token"

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(token_url, data=data, timeout=20)

    if response.status_code != 200:
        return f"Erro ao obter token: {response.text}"

    token_data = response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    # Não exibimos os tokens na tela por segurança.
    if access_token:
        return """
        <h2>Mercado Livre autorizado com sucesso!</h2>
        <p>O Access Token foi recebido corretamente.</p>
        <p>Próximo passo: salvar os tokens de forma segura para o bot usar.</p>
        """

    return "Autorização concluída, mas o token não foi encontrado."
