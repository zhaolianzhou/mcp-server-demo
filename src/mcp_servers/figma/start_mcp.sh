cd Figma-Context-MCP
pnpm install
pnpm build
#pnpm start:http -- --figma-api-key="$FIGMA_API_KEY" --port 3333
pnpm start:http -- --figma-oauth-token="$FIGMA_OAUTH_TOKEN" --port 3333

