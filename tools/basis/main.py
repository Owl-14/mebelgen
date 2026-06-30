#!/usr/bin/env python3
"""CLI: конвертация изображения мебели в JSON для БАЗИС-Мебельщик."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.validate import validate_file


def cmd_convert(args: argparse.Namespace) -> int:
    from src.converter import FurnitureConverter

    converter = FurnitureConverter(model=args.model)
    out = Path(args.output) if args.output else Path(args.image).with_suffix(".json")
    data = converter.convert_to_file(
        Path(args.image),
        out,
        extra_instructions=args.instructions or "",
        temperature=args.temperature,
    )
    if args.print:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"Сохранено: {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from src.geometry_check import run_geometry_pipeline

    path = Path(args.json)
    errors = validate_file(path)
    if errors:
        print("Ошибки валидации:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"OK (схема): {args.json}")

    if not getattr(args, "geometry", False):
        return 0

    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    report = run_geometry_pipeline(
        data,
        min_volume=args.min_overlap_volume,
        auto_fix_horizontal=args.fix_horizontal,
    )
    if report["ok"]:
        print(f"OK (геометрия): {report['panel_count']} панелей, пересечений нет.")
        if report.get("fixes_applied"):
            for line in report["fixes_applied"]:
                print(f"  исправлено: {line}")
            if args.fix_horizontal and report.get("data_after_fix"):
                print(
                    "Подсказка: запишите результат через "
                    "`check-geometry --fix-horizontal -o …` (см. RULES.md §6).",
                    file=sys.stderr,
                )
        return 0

    print("Пересечения панелей:", file=sys.stderr)
    for o in report["overlaps"]:
        print(
            f"  - {o['panel_a']} <-> {o['panel_b']}: "
            f"{o['intersection_volume_mm3']} мм³ — {o['suggested_action']}",
            file=sys.stderr,
        )
        print(f"    {o['detail']}", file=sys.stderr)
    return 2


def cmd_check_geometry(args: argparse.Namespace) -> int:
    from src.geometry_check import (
        OverlapIssue,
        check_placement_geometry,
        format_overlap_report,
        resolve_horizontal_splits,
    )

    path = Path(args.json)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    if args.fix_horizontal:
        data, fixes = resolve_horizontal_splits(data, min_volume=args.min_overlap_volume)
        for line in fixes:
            print(line)

    report = check_placement_geometry(data, min_volume=args.min_overlap_volume)
    issues = [
        OverlapIssue(
            panel_a=o["panel_a"],
            panel_b=o["panel_b"],
            orientation_a=o["orientation_a"],
            orientation_b=o["orientation_b"],
            intersection_volume_mm3=o["intersection_volume_mm3"],
            suggested_action=o["suggested_action"],
            detail=o["detail"],
        )
        for o in report["overlaps"]
    ]
    print(format_overlap_report(issues))
    print(f"Панелей с placement: {report['panel_count']}, пересечений: {report['overlap_count']}")

    if args.output:
        out_path = Path(args.output)
        if args.fix_horizontal or report["ok"]:
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Записано: {out_path}")
        else:
            print("JSON не записан: остались пересечения (исправьте вручную или уточните ТЗ).", file=sys.stderr)

    return 0 if report["ok"] else 2


def cmd_build_b3d(args: argparse.Namespace) -> int:
    from src.build_b3d import build_b3d, build_b3d_from_paramspec

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out = args.output or str(Path(args.input).with_suffix(".b3d"))
    if data.get("schemaVersion") == "paramspec-v1":
        res = build_b3d_from_paramspec(data, out)
    else:
        res = build_b3d(data, out)
    print(f"Готов .b3d: {res['b3d']}  ({res['bytes']} байт, task {res['task_id']})")
    return 0


def cmd_cloud(args: argparse.Namespace) -> int:
    from src.cloud_api import (CloudTasksClient, DRAWING_FORMAT, MODEL_CONVERT, api_overview)

    if args.op == "info":
        print(api_overview())
        return 0
    c = CloudTasksClient()
    if args.op == "list":
        print(json.dumps(c.list_tasks(), ensure_ascii=False, indent=2))
    elif args.op == "status":
        print(json.dumps(c.get_task(int(args.args[0])), ensure_ascii=False, indent=2))
    elif args.op == "poll":
        print(json.dumps(c.poll(int(args.args[0])), ensure_ascii=False, indent=2))
    elif args.op == "download":
        print("Сохранено:", c.download_result(int(args.args[0]), args.output or "result.bin"))
    elif args.op == "model-convert":
        print(json.dumps(c.model_convert(args.args, MODEL_CONVERT[args.type]), ensure_ascii=False, indent=2))
    elif args.op == "drawing-convert":
        print(json.dumps(c.drawing_convert(args.args, DRAWING_FORMAT[args.format]), ensure_ascii=False, indent=2))
    return 0


def cmd_cutting(args: argparse.Namespace) -> int:
    from src.cloud_cutting import CuttingClient, api_overview

    if args.op == "info":
        print(api_overview())
        return 0
    c = CuttingClient()
    a = args.args
    out = None
    if args.op == "orders":
        out = c.list_orders()
    elif args.op == "order":
        out = c.get_order(int(a[0]))
    elif args.op == "details":
        out = c.order_details(int(a[0]))
    elif args.op == "specification":
        out = c.order_specification(int(a[0]))
    elif args.op == "cad-models":
        out = c.list_cad_models(int(a[0]))
    elif args.op == "upload":
        out = c.upload_cad_model(int(a[0]), a[1])
    elif args.op == "materials":
        out = c.cad_model_materials(int(a[0]))
    elif args.op == "run-production":
        out = c.run_production_files(int(a[0]))
    elif args.op == "production-url":
        out = c.production_files_url(int(a[0]))
    elif args.op == "long-tasks":
        out = c.list_long_tasks()
    elif args.op == "long-task":
        out = c.long_task(int(a[0]))
    print(json.dumps(out, ensure_ascii=False, indent=2) if not isinstance(out, str) else out)
    return 0


def cmd_materials(args: argparse.Namespace) -> int:
    from src.materials import check_project_materials, load_catalog

    cat = load_catalog()
    n = sum(len(cat.get(k, [])) for k in ("boards", "backs", "edges", "hardware"))
    print(f"Каталог: {n} позиций (boards/backs/edges/hardware).")
    if args.check:
        project = json.loads(Path(args.check).read_text(encoding="utf-8"))
        notes = check_project_materials(project)
        if not notes:
            print("Материалы проекта сопоставлены с каталогом — замечаний нет.")
        else:
            print("Замечания по материалам:")
            for x in notes:
                print(f"  • {x}")
    return 0


def cmd_orchestrate(args: argparse.Namespace) -> int:
    from src.orchestrator import orchestrate_file

    res = orchestrate_file(args.paramspec, args.output)
    print(res.report())
    return 0 if res.ok else 2


def cmd_ingest(args: argparse.Namespace) -> int:
    from src.feedback import record_pair

    spec = json.loads(Path(args.paramspec).read_text(encoding="utf-8"))
    project = json.loads(Path(args.project).read_text(encoding="utf-8"))
    path = record_pair(spec, project, source_tz=args.tz or "")
    n = len(list(path.parent.glob("*.json")))
    print(f"Записана обучающая пара: {path}  (всего в датасете: {n})")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    from src.paramspec import validate_paramspec
    from src.generators import generate_from_paramspec
    from src.validate import validate_furniture
    from src.geometry_check import check_placement_geometry
    from src.consistency_check import check_consistency, format_consistency_report

    spec_path = Path(args.paramspec)
    with spec_path.open(encoding="utf-8") as f:
        spec = json.load(f)

    spec_errors = validate_paramspec(spec)
    if spec_errors:
        print("Ошибки ParamSpec:", file=sys.stderr)
        for e in spec_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    project = generate_from_paramspec(spec)

    out = Path(args.output) if args.output else spec_path.parent.parent / "projects" / spec_path.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Сгенерировано: {out}  (archetype={spec['archetype']}, панелей: {len(project['panels'])})")

    schema_errors = validate_furniture(project)
    geom = check_placement_geometry(project)
    cons = check_consistency(project)
    schema_str = "OK" if not schema_errors else "ОШИБКА"
    geom_str = "OK" if geom["ok"] else f"{geom['overlap_count']} пересечений"
    print(f"  схема: {schema_str}; геометрия: {geom_str}; согласованность: {len(cons)}")
    if schema_errors:
        for e in schema_errors:
            print(f"   - {e}", file=sys.stderr)
    if cons:
        print(format_consistency_report(cons), file=sys.stderr)
    return 0 if (not schema_errors and geom["ok"] and not cons) else 2


def cmd_check_consistency(args: argparse.Namespace) -> int:
    from src.consistency_check import (
        check_consistency,
        format_consistency_report,
        has_errors,
    )
    from src.geometry_check import recompute_dimensions_from_placement

    path = Path(args.json)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    if args.fix_dimensions:
        data, log = recompute_dimensions_from_placement(data)
        for line in log:
            print(f"dimensions: {line}")
        if args.output:
            Path(args.output).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Записано: {args.output}")
        elif log:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Обновлено на месте: {path}")

    issues = check_consistency(data)
    print(format_consistency_report(issues))
    return 2 if has_errors(issues) else 0


def cmd_finish(args: argparse.Namespace) -> int:
    from src.finish_project import finish_project

    path = Path(args.json)
    result = finish_project(
        path,
        min_volume=args.min_overlap_volume,
    )
    print(result.summary())
    if not result.schema_ok:
        print("\nОшибки схемы:", file=sys.stderr)
        for e in result.schema_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if not result.geometry_ok:
        print("\nТребуется правка по ТЗ (вертикали и пр.)", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Конвертер мебельной документации → JSON для БАЗИС-Мебельщик"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="Конвертировать изображение в JSON")
    p_convert.add_argument("image", help="Путь к изображению (png/jpg/webp)")
    p_convert.add_argument("-o", "--output", help="Путь к выходному JSON")
    p_convert.add_argument("-i", "--instructions", help="Доп. текст к промпту")
    p_convert.add_argument("--model", default=None, help="Модель OpenAI (по умолчанию gpt-4o)")
    p_convert.add_argument("--temperature", type=float, default=0.1)
    p_convert.add_argument("--print", action="store_true", help="Вывести JSON в stdout")
    p_convert.set_defaults(func=cmd_convert)

    p_validate = sub.add_parser("validate", help="Проверить JSON по схеме")
    p_validate.add_argument("json", help="Путь к JSON-файлу")
    p_validate.add_argument(
        "--geometry",
        action="store_true",
        help="После схемы проверить пересечения панелей (placement)",
    )
    p_validate.add_argument(
        "--fix-horizontal",
        action="store_true",
        help="С --geometry: разделить горизонтали, пересекающие стойки",
    )
    p_validate.add_argument(
        "--min-overlap-volume",
        type=float,
        default=1.0,
        help="Игнорировать пересечения меньше этого объёма (мм³)",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_geom = sub.add_parser(
        "check-geometry",
        help="Проверка пересечений placement; опционально разделение полок",
    )
    p_geom.add_argument("json", help="Путь к JSON-файлу")
    p_geom.add_argument(
        "-o",
        "--output",
        help="Записать JSON (после --fix-horizontal или если пересечений нет)",
    )
    p_geom.add_argument(
        "--fix-horizontal",
        action="store_true",
        help="Разделить горизонтали по стойкам и состыковать по X",
    )
    p_geom.add_argument(
        "--min-overlap-volume",
        type=float,
        default=1.0,
        help="Порог объёма пересечения (мм³)",
    )
    p_geom.set_defaults(func=cmd_check_geometry)

    p_gen = sub.add_parser(
        "generate",
        help="ParamSpec → project.json (детерминированный генератор архетипа)",
    )
    p_gen.add_argument("paramspec", help="Путь к ParamSpec JSON (paramspecs/<x>.json)")
    p_gen.add_argument("-o", "--output", help="Куда писать project.json (по умолчанию projects/<имя>)")
    p_gen.set_defaults(func=cmd_generate)

    p_b3d = sub.add_parser("build-b3d", help="ParamSpec/project → .cfrn → облако → нативный .b3d (ПЛАТНО ~10₽, env BAZIS_API_KEY)")
    p_b3d.add_argument("input", help="ParamSpec или project.json")
    p_b3d.add_argument("-o", "--output", help="Путь к .b3d (по умолчанию рядом с входом)")
    p_b3d.set_defaults(func=cmd_build_b3d)

    p_cloud = sub.add_parser("cloud", help="БАЗИС-Облако Tasks API (env BAZIS_API_KEY; 'cloud info' без ключа)")
    p_cloud.add_argument("op", choices=["info", "list", "status", "poll", "download", "model-convert", "drawing-convert"])
    p_cloud.add_argument("args", nargs="*", help="id или файлы")
    p_cloud.add_argument("-o", "--output", help="куда сохранить результат")
    p_cloud.add_argument("--type", choices=["b3d-to-cfrn", "cfrn-to-b3d"], help="для model-convert")
    p_cloud.add_argument("--format", choices=["pdf", "jpeg", "wmf", "svg"], help="для drawing-convert")
    p_cloud.set_defaults(func=cmd_cloud)

    p_cut = sub.add_parser("cutting", help="БАЗИС-Облако Cutting API раскрой/производство ('cutting info' без ключа)")
    p_cut.add_argument("op", choices=["info", "orders", "order", "details", "specification", "cad-models",
                                      "upload", "materials", "run-production", "production-url", "long-tasks", "long-task"])
    p_cut.add_argument("args", nargs="*", help="id заказа/модели; для upload: orderId file")
    p_cut.set_defaults(func=cmd_cutting)

    p_mat = sub.add_parser("materials", help="Каталог материалов; --check сверить материалы проекта")
    p_mat.add_argument("--check", help="project.json для сверки материалов с каталогом")
    p_mat.set_defaults(func=cmd_materials)

    p_orc = sub.add_parser(
        "orchestrate",
        help="ParamSpec → генерация → валидаторы → авторемонт → само-ревью (единый прогон)",
    )
    p_orc.add_argument("paramspec", help="Путь к ParamSpec JSON")
    p_orc.add_argument("-o", "--output", help="Куда писать project.json (по умолчанию projects/<имя>)")
    p_orc.set_defaults(func=cmd_orchestrate)

    p_ing = sub.add_parser(
        "ingest",
        help="Записать принятый проект как обучающую пару (ParamSpec→project) в dataset/",
    )
    p_ing.add_argument("--paramspec", required=True, help="ParamSpec, по которому строили")
    p_ing.add_argument("--project", required=True, help="Принятый/исправленный project.json")
    p_ing.add_argument("--tz", help="Текст исходного ТЗ (опционально)")
    p_ing.set_defaults(func=cmd_ingest)

    p_cons = sub.add_parser(
        "check-consistency",
        help="Согласованность placement↔dimensions↔thickness↔габарит (не пересечения)",
    )
    p_cons.add_argument("json", help="Путь к JSON-файлу")
    p_cons.add_argument(
        "--fix-dimensions",
        action="store_true",
        help="Пересчитать dimensions из placement (источник истины)",
    )
    p_cons.add_argument(
        "-o",
        "--output",
        help="С --fix-dimensions: записать в этот файл (иначе на месте)",
    )
    p_cons.set_defaults(func=cmd_check_consistency)

    p_finish = sub.add_parser(
        "finish",
        help="Схема + геометрия + автоисправление полок (для агента, не для пользователя)",
    )
    p_finish.add_argument("json", help="Путь к JSON (обычно projects/<project>.json)")
    p_finish.add_argument(
        "--min-overlap-volume",
        type=float,
        default=1.0,
        help="Порог объёма пересечения (мм³)",
    )
    p_finish.set_defaults(func=cmd_finish)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
