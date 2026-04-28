FROM node:20-alpine

RUN npm install -g pnpm@9.12.0

WORKDIR /app

COPY package.json ./
RUN pnpm install

COPY . .

EXPOSE 3000
CMD ["pnpm", "dev"]
