
### Start OAuth server
```aiexclude
poetry install
poetry shell
cd src
poetry run python main.py
```

Add client id and secret to each oauth provider.

### Slack OAuth
Open a separate terminal:
```aiignore
brew install ngrok
ngrok config add-authtoken <ngrok token>
ngrok http --domain=zhaolian-local.ninjatech.ngrok.dev 8000
```

Go to `http://0.0.0.0:8000/slack/install`