# Portable Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** надёжный Start/Stop одной portable-установки без потери старых Docker-данных.
**Architecture:** сохранить существующий launcher и Docker volumes; обнаруживать управляемые ресурсы по labels, сохранять остановленные предыдущие контейнеры переименованием, переиспользовать собственную сеть.
**Tech Stack:** Python stdlib, Docker CLI, pytest.
**Spec:** запрос владельца 2026-10-24, PR35 77261fc8069ef5d0621fdbe4356d0a9e3cbb6e0e.

## Global Constraints
- Не удалять существующие containers/volumes; не включать classic автоматически.
- Не вызывать платный Venice; секреты не выводить, не сохранять в Git.
- Один корень D:\NoClip\Shluz; сетевые проверки и verified backup выполняет основной агент.
- Здесь изменять только miniapp_live.py и его тесты; main не объединять.

## Review Focus
- Пропавший/повреждённый journal не мешает Stop.
- Ошибка одного ресурса не оставляет другие управляемые сервисы запущенными.
- Чужие labels и подставленный old_id не разрешают остановку чужого контейнера.
- Ошибка до готовности выключает весь управляемый runtime, не включает classic.
- Повторный Start сохраняет предыдущие containers, сеть и те же volumes.

## Tasks
- [x] Добавить регрессии Stop missing/corrupt state, ownership и продолжения после ошибки; подтвердить RED.
- [x] Реализовать обнаружение/остановку без journal, сохранить чужие ресурсы.
- [x] Добавить регрессии failure cleanup, explicit rollback, retained resources; подтвердить RED.
- [x] Реализовать journal до первого изменения, cleanup без classic, rename/reuse вместо rm.
- [x] Запустить tests/launcher/test_miniapp_live.py и связанные launcher tests; один локальный commit без push.

## Проверено локально
- RED: missing/corrupt state, continuation, forged journal, no-remove, retained resources, loaded image и auto protocol сначала падали.
- GREEN: 22 tests miniapp_live passed, Ruff двух выделенных файлов clean.
- Общий launcher suite запускает основной агент в окружении с PyYAML; живой Docker/Telegram этой подзадачей не запускался.
