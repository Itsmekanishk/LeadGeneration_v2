FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=""
ARG INTERNAL_API_URL="http://api:8000"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV INTERNAL_API_URL=$INTERNAL_API_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -g 10001 nodejs && adduser -u 10001 -G nodejs -S nextjs
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER nextjs
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
