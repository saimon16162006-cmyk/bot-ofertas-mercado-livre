import os
from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot de Ofertas Mercado Livre ONLINE!"

@app.route("/callback")
def callback():
    code = request.args.get("code")

    if code:
        return f"Autorizacao do Mercado Livre recebida com sucesso! Codigo: {code}"

    return "Callback do Mercado Livre funcionando!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
