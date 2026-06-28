# Built from the repo root: docker build -f apps/api/Dockerfile .
FROM node:22-slim
WORKDIR /app
RUN corepack enable
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json turbo.json ./
COPY packages/shared ./packages/shared
COPY apps/api ./apps/api
RUN pnpm install --frozen-lockfile \
    && pnpm --filter @myagent/shared build \
    && pnpm --filter @myagent/api build
ENV PORT=3001
EXPOSE 3001
CMD ["node", "apps/api/dist/index.js"]
