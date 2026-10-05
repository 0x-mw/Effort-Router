#!/usr/bin/env python3
"""check_tier_table.py 오프라인 시험 — 임시 스킬 폴더만 사용."""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_tier_table as ct  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  → {detail}"))


TABLE = """| 업무(스킬) | 기본 티어 | 완료 게이트(파이썬 검증) | 출처 |
|---|---|---|---|
| 견적서 (klic-quote) | D2 | 3종 교차검증, 요율 역산, 절사 | 규칙 |
| 제안서 검토 (proposal-landscape, proposal-assemble) | D3 | 좌표·XML 10절 검증, 요구사항 누락 | 규칙 |
| 총괄표(xlsx)·PPT (klic-quote) | D3 | 교차검증 | ★ |
| 변환 (hwp) | D1 | 왕복 변환 확인(Markdown→HWPX 시) | 규칙 |
"""


def run(table_text, skills, extra=()):
    with tempfile.TemporaryDirectory() as t:
        sd = Path(t) / "skills"
        for name, body in skills.items():
            (sd / name).mkdir(parents=True)
            (sd / name / "SKILL.md").write_text(body, encoding="utf-8")
        tf = Path(t) / "table.md"; tf.write_text(table_text, encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ct.main(["--table", str(tf), "--skills-dir", str(sd), *extra])
        return rc, buf.getvalue()


OK_SKILLS = {"klic-quote": "교차검증 요율 절사", "proposal-landscape": "10절 검증 좌표",
             "proposal-assemble": "요구사항 누락", "hwp": "왕복 변환"}
rows = ct.parse_rows(TABLE)
check("표 행 4개·스킬 이름 추출(끝 괄호만, 중간의 (xlsx)는 스킬 아님)",
      len(rows) == 4 and rows[1][1] == ["proposal-landscape", "proposal-assemble"], str(rows))
check("게이트 항목 분리(괄호 안 쉼표는 쪼개지 않음)", len(rows[0][2]) == 3 and rows[2][1] == ["klic-quote"] and "왕복 변환 확인(Markdown→HWPX 시)" in rows[3][2], str(rows))

rc, out = run(TABLE, OK_SKILLS)
check("모두 있으면 종료코드 0·오류 0", rc == 0 and "오류 0건" in out, out)

rc, out = run(TABLE, {k: v for k, v in OK_SKILLS.items() if k != "hwp"})
check("스킬 폴더가 없으면 오류(종료코드 1)", rc == 1 and "스킬 `hwp`" in out, out)

bad = dict(OK_SKILLS, **{"klic-quote": "전혀 다른 내용"})
rc, out = run(TABLE, bad)
check("핵심어가 본문에 없으면 경고(기본은 종료코드 0)", rc == 0 and "경고: [견적서" in out, out)
rc, _ = run(TABLE, bad, ["--strict"])
check("--strict 면 경고도 오류(종료코드 1)", rc == 1, str(rc))

rc, out = run("표가 없는 문서", OK_SKILLS)
check("표 형식이 없으면 종료코드 2", rc == 2, out)
rc = ct.main(["--table", "/없는/파일.md"])
check("표 파일이 없으면 종료코드 2", rc == 2, str(rc))

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
