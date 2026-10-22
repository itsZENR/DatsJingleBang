# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DatsJingleBang — бот для участия в геймтоне DatSteam. Игра в стиле Bomberman: управление юнитами, установка бомб, уничтожение препятствий и соперников.

## API

Base URL: `https://games-test.datsteam.dev` (test) / production определяется через `/api/rounds`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/arena` | GET | Текущее состояние арены |
| `/api/move` | POST | Команды движения и установки бомб |
| `/api/booster` | POST | Покупка улучшений |
| `/api/rounds` | GET | Расписание раундов |
| `/api/logs` | GET | Логи игры |
| `/api/cheatcode` | POST | Ввод чит-кода |

Авторизация: заголовок с токеном (см. документацию).

## Game Mechanics

- Движение: путь = список координат (max 30)
- Бомбы: устанавливаются вместе с движением, взрыв крестом
- Цепочки: макс очки 1+2+3+4=10 за цепную детонацию
- Мобы: патрулируют, преследуют при обнаружении, касание = смерть

## Documentation

- `STRATEGY.md` — стратегия и советы по игре

## Запуск бота

```bash
# Перейти в директорию bomberman (родительская для пакета)
cd E:\EyeSky\DatsJingleBang\Gameton\bomberman

# Запуск бота
python -m bomberman.main 

# Или с --prod для production сервера
```
