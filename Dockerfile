FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY dist/ ./dist/
COPY src/db/schema.sql ./dist/db/schema.sql
ENV PORT=8080
EXPOSE 8080
CMD ["node", "dist/index.js"]
