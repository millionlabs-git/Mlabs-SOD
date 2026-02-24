FROM node:20-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY --from=builder /app/dist ./dist
COPY src/db/schema.sql ./dist/db/schema.sql
ENV PORT=8080
EXPOSE 8080
CMD ["node", "dist/index.js"]
