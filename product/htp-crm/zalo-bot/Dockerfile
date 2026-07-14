FROM node:20-slim

ENV TZ=Asia/Ho_Chi_Minh
WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY bot.js ./

CMD ["node", "bot.js"]
