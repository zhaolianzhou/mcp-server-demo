python3.11 -m venv venv                                                                                                                  <aws:dev>
source venv/bin/activate
pip install -r requirements.txt

python3.11 server.py

poetry run python klavis_slack_sse_mcp_validator.py --base-url http://localhost:5000 --sse-path /sse/
poetry run python klavis_slack_http_mcp_validator.py --base-url http://localhost:5000 --path /mcp/