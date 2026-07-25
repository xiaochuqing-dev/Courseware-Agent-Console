from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import (  # noqa: E402
    AcceptanceService,
    ArchiveService,
    FeedbackService,
    ProjectService,
    TaskService,
    ToolBinding,
)


LESSON_TYPES = {
    "立体表面最短路径": "cylinder",
    "数轴上点的运动": "numberline",
    "太阳光线下物体影子的变化规律。": "sunshadow",
    "小棒和纸片在手电筒照射下的影子变化": "projection",
    "正方形纸片的翻折问题": "folding",
    "正负球模型（有理数加法）": "balls",
}

CONTROL_SPECS = {
    "cylinder": [
        ("cylinderHeight", "圆柱高", 3, 8, 0.5, " 格"),
        ("circumference", "底面周长", 6, 14, 0.5, " 格"),
    ],
    "numberline": [
        ("startValue", "起点", -6, 6, 1, ""),
        ("moveDistance", "移动距离", 1, 8, 1, " 格"),
    ],
    "sunshadow": [
        ("sunAngle", "太阳高度角", 18, 78, 1, "°"),
        ("sunSide", "太阳方向", -1, 1, 2, ""),
    ],
    "projection": [
        ("lightHeight", "光源高度", 3, 8, 0.5, " 格"),
        ("objectX", "物体位置", -3, 3, 0.5, " 格"),
        ("projectionMode", "投影方式", 0, 1, 1, ""),
    ],
    "folding": [
        ("foldPosition", "点 E 位置", 0.58, 0.9, 0.01, ""),
    ],
    "balls": [
        ("positiveCount", "正球数量", 0, 10, 1, " 个"),
        ("negativeCount", "负球数量", 0, 10, 1, " 个"),
    ],
}

LOGIC = {
    "cylinder": [
        ["先观察：点 A、B 位于圆柱侧面", "目标：比较曲面上的不同路线"],
        ["根据：沿母线剪开圆柱侧面", "得到：曲面展开为矩形"],
        ["两点之间线段最短", "最短路径 = √(高² + 底面周长²)"],
    ],
    "numberline": [
        ["数轴三要素：原点、正方向、单位长度", "点的位置对应一个有理数"],
        ["向右运动，数值增大", "终点 = 起点 + 移动距离"],
        ["向左运动，数值减小", "终点 = 起点 - 移动距离"],
        ["先定起点，再定方向和距离", "用加法统一表示点的运动"],
    ],
    "sunshadow": [
        ["光线、物高和影子构成直角三角形", "太阳越高，影子越短", "太阳在哪侧，影子投向相反方向"],
    ],
    "projection": [
        ["光源、物体和投影面共同决定影子", "先分清光线从哪里发出"],
        ["中心投影：光线汇聚于一点", "物体靠近点光源时影子变大"],
        ["平行投影：各条投射线互相平行", "正投影时光线垂直于投影面"],
    ],
    "folding": [
        ["已知：ABCD 是正方形", "四边相等，四个角都是 90°"],
        ["翻折是轴对称变换", "得到：EA = EA′"],
        ["△EBA′ 是直角三角形", "根据勾股定理列式求线段长度"],
    ],
    "balls": [
        ["1 个蓝球表示 +1", "1 个橙球表示 −1"],
        ["互为相反数的两个数相加得 0", "一蓝一橙可以配对抵消"],
        ["抵消后剩余小球决定结果", "剩蓝球为正，剩橙球为负"],
        ["算式、球数和抵消过程同步", "用多组例子归纳异号加法法则"],
    ],
}


DRAWING_SCRIPT = r'''
    function drawText(text, x, y, size, color, align) {
      ctx.save();
      ctx.fillStyle = color || "#20313a";
      ctx.font = "900 " + (size || 24) + "px Microsoft YaHei, sans-serif";
      ctx.textAlign = align || "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, x, y);
      ctx.restore();
    }

    function drawArrow(a, b, color, width) {
      drawLine(a, b, color, width || 4, null, 1);
      var angle = Math.atan2(b.y - a.y, b.x - a.x);
      var size = 14;
      drawLine(b, vec(b.x - size * Math.cos(angle - 0.55), b.y - size * Math.sin(angle - 0.55)), color, width || 4, null, 1);
      drawLine(b, vec(b.x - size * Math.cos(angle + 0.55), b.y - size * Math.sin(angle + 0.55)), color, width || 4, null, 1);
    }

    function drawCylinderLesson(progress) {
      var box = sceneBox();
      var w = box.right - box.left;
      var h = box.bottom - box.top;
      var leftCenter = vec(box.left + w * 0.28, box.top + h * 0.5);
      var radius = Math.min(w, h) * 0.13;
      var bodyHeight = Math.min(h * 0.52, state.cylinderHeight * h * 0.065);
      var topY = leftCenter.y - bodyHeight / 2;
      var bottomY = leftCenter.y + bodyHeight / 2;
      ctx.save();
      ctx.fillStyle = "rgba(19,125,137,0.11)";
      ctx.fillRect(leftCenter.x - radius, topY, radius * 2, bodyHeight);
      ctx.strokeStyle = "#137d89";
      ctx.lineWidth = 5;
      ctx.strokeRect(leftCenter.x - radius, topY, radius * 2, bodyHeight);
      ctx.beginPath(); ctx.ellipse(leftCenter.x, topY, radius, radius * 0.28, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.ellipse(leftCenter.x, bottomY, radius, radius * 0.28, 0, 0, Math.PI); ctx.stroke();
      ctx.restore();
      var A = vec(leftCenter.x - radius, topY);
      var B = vec(leftCenter.x + radius, bottomY);
      drawPoint(A, "A", "#c97918", 1);
      drawPoint(B, "B", "#c97918", 1);
      if (state.step >= 1) {
        drawLine(vec(leftCenter.x - radius, topY), vec(leftCenter.x - radius, bottomY), "#705bc2", 4, [12, 8], 1);
        drawText("沿母线剪开", leftCenter.x, bottomY + 55, 21, "#5c4b9b");
      }
      if (state.step >= 1) {
        var reveal = state.step === 1 ? progress : 1;
        var rw = Math.min(w * 0.44, state.circumference * w * 0.032) * reveal;
        var rh = bodyHeight;
        var rx = box.left + w * 0.52;
        var ry = leftCenter.y - rh / 2;
        ctx.fillStyle = "rgba(201,121,24,0.09)";
        ctx.fillRect(rx, ry, rw, rh);
        ctx.strokeStyle = "#c97918";
        ctx.lineWidth = 5;
        ctx.strokeRect(rx, ry, rw, rh);
        if (reveal > 0.4) {
          var pA = vec(rx, ry);
          var pB = vec(rx + rw, ry + rh);
          drawPoint(pA, "A", "#137d89", 1);
          drawPoint(pB, "B", "#137d89", 1);
          if (state.step >= 2) {
            drawLine(pA, pB, "#137d89", 6, null, 1);
            var distance = Math.sqrt(state.cylinderHeight * state.cylinderHeight + state.circumference * state.circumference);
            drawText("AB = " + distance.toFixed(2) + " 格", rx + rw / 2, ry + rh / 2 - 26, 24, "#137d89");
            drawRightAngleMark(vec(rx, ry + rh), vec(rw, 0), vec(0, -rh), 20);
          }
        }
      }
    }

    function drawNumberlineLesson(progress) {
      var box = sceneBox();
      var y = box.top + (box.bottom - box.top) * 0.54;
      var left = box.left + 40;
      var right = box.right - 40;
      drawArrow(vec(left, y), vec(right, y), "#20313a", 4);
      var unit = (right - left) / 20;
      for (var n = -9; n <= 9; n += 1) {
        var x = left + (n + 10) * unit;
        drawLine(vec(x, y - 13), vec(x, y + 13), "#20313a", n === 0 ? 4 : 2, null, 1);
        drawText(String(n), x, y + 40, 17, n === 0 ? "#c97918" : "#52656b");
      }
      var direction = state.step === 2 ? -1 : state.step >= 3 ? (state.moveDirection || 1) : 1;
      var animated = state.step === 0 ? 0 : progress;
      var value = state.startValue + direction * state.moveDistance * animated;
      var px = left + (value + 10) * unit;
      drawPoint(vec(px, y), "P", "#137d89", 1);
      drawText("P = " + value.toFixed(1), px, y - 70, 27, "#137d89");
      if (state.step > 0) {
        var operator = direction > 0 ? " + " : " − ";
        var result = state.startValue + direction * state.moveDistance;
        drawText(state.startValue + operator + state.moveDistance + " = " + result, (left + right) / 2, box.top + 70, 30, "#c97918");
      }
    }

    function drawSunshadowLesson() {
      var box = sceneBox();
      var groundY = box.bottom - 95;
      var baseX = (box.left + box.right) / 2;
      var treeHeight = (box.bottom - box.top) * 0.42;
      drawLine(vec(box.left + 20, groundY), vec(box.right - 20, groundY), "#51665e", 5, null, 1);
      drawLine(vec(baseX, groundY), vec(baseX, groundY - treeHeight), "#755535", 18, null, 1);
      ctx.fillStyle = "#4b966d";
      ctx.beginPath(); ctx.arc(baseX, groundY - treeHeight, 52, 0, Math.PI * 2); ctx.fill();
      var angle = state.sunAngle * Math.PI / 180;
      var side = state.sunSide < 0 ? -1 : 1;
      var sunRadius = Math.min(box.right - box.left, box.bottom - box.top) * 0.35;
      var sun = vec(baseX + side * Math.cos(angle) * sunRadius, groundY - Math.sin(angle) * sunRadius);
      ctx.fillStyle = "#f2bf3f"; ctx.beginPath(); ctx.arc(sun.x, sun.y, 32, 0, Math.PI * 2); ctx.fill();
      var shadowLength = 3 / Math.tan(angle);
      var scale = Math.min(95, (box.right - box.left) * 0.055);
      var end = vec(baseX - side * shadowLength * scale, groundY);
      drawLine(vec(baseX, groundY), end, "rgba(32,49,58,0.76)", 13, null, 1);
      drawArrow(sun, vec(baseX, groundY), "#d89b20", 4);
      drawText("影长 " + shadowLength.toFixed(2) + " m", end.x, groundY - 34, 23, "#20313a");
    }

    function drawProjectionLesson(progress) {
      var box = sceneBox();
      var groundY = box.bottom - 90;
      var centerX = (box.left + box.right) / 2 + state.objectX * 36;
      var objectHeight = 150;
      drawLine(vec(box.left + 10, groundY), vec(box.right - 10, groundY), "#51665e", 5, null, 1);
      drawLine(vec(centerX, groundY), vec(centerX, groundY - objectHeight), "#8b6845", 16, null, 1);
      var parallel = state.step >= 2 || state.projectionMode >= 1;
      if (!parallel) {
        var light = vec(box.left + 120, groundY - state.lightHeight * 58);
        ctx.fillStyle = "#f2bf3f"; ctx.beginPath(); ctx.arc(light.x, light.y, 30, 0, Math.PI * 2); ctx.fill();
        var ratio = (groundY - light.y) / Math.max(30, groundY - objectHeight - light.y);
        var endX = light.x + (centerX - light.x) * ratio;
        drawLine(light, vec(endX, groundY), "#d89b20", 4, null, 1);
        drawLine(vec(centerX, groundY), vec(endX, groundY), "rgba(32,49,58,0.76)", 13, null, 1);
        drawText("中心投影", box.left + 150, box.top + 60, 28, "#c97918");
      } else {
        var dx = 120;
        for (var i = -1; i <= 1; i += 1) {
          drawArrow(vec(centerX - 160 + i * 70, groundY - 300), vec(centerX - 40 + i * 70, groundY - 30), "#d89b20", 4);
        }
        drawLine(vec(centerX, groundY), vec(centerX + dx, groundY), "rgba(32,49,58,0.76)", 13, null, 1);
        drawText("平行投影", box.left + 150, box.top + 60, 28, "#137d89");
      }
    }

    function drawFoldingLesson(progress) {
      var box = sceneBox();
      var side = Math.min(box.right - box.left, box.bottom - box.top) * 0.56;
      var left = (box.left + box.right - side) / 2;
      var top = (box.top + box.bottom - side) / 2;
      var A = vec(left, top), B = vec(left + side, top), C = vec(left + side, top + side), D = vec(left, top + side);
      drawPolygon([A, B, C, D], "rgba(201,121,24,0.09)", "#c97918", 6, 1);
      drawPoint(A, "A", "#137d89", 1); drawPoint(B, "B", "#137d89", 1); drawPoint(C, "C", "#137d89", 1); drawPoint(D, "D", "#137d89", 1);
      if (state.step >= 1) {
        var E = vec(left + side * state.foldPosition, top);
        var ea = E.x - A.x;
        var ba = Math.sqrt(Math.max(0, ea * ea - (B.x - E.x) * (B.x - E.x)));
        var Aprime = vec(B.x, top + ba);
        var normal = sub(Aprime, A);
        var direction = vec(-normal.y, normal.x);
        var scale = (D.y - E.y) / direction.y;
        var F = add(E, mul(direction, scale));
        var foldProgress = state.step === 1 ? progress : 1;
        var movingA = add(A, mul(sub(Aprime, A), foldProgress));
        drawLine(E, F, "#705bc2", 4, [12, 8], 1);
        drawPoint(E, "E", "#705bc2", 1); drawPoint(F, "F", "#705bc2", 1);
        drawPoint(movingA, "A′", "#137d89", 1);
        drawLine(E, movingA, "#137d89", 5, null, 1);
        if (state.step >= 2) {
          drawPolygon([E, B, Aprime], "rgba(19,125,137,0.16)", "#137d89", 4, 1);
          drawRightAngleMark(B, sub(E, B), sub(Aprime, B), 22);
          drawText("EA = EA′", left + side * 0.5, top + side + 52, 26, "#137d89");
        }
      }
    }

    function drawBallsLesson(progress) {
      var box = sceneBox();
      var pos = Math.round(state.positiveCount);
      var neg = Math.round(state.negativeCount);
      var paired = Math.min(pos, neg);
      var fade = state.step >= 2 ? progress : 0;
      function ball(x, y, color, label, alpha) {
        ctx.save(); ctx.globalAlpha = alpha; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 28, 0, Math.PI * 2); ctx.fill(); drawText(label, x, y, 24, "#fff"); ctx.restore();
      }
      var startX = box.left + 90;
      var spacing = Math.min(74, (box.right - box.left - 160) / 9);
      for (var i = 0; i < pos; i += 1) {
        var alphaP = i < paired ? 1 - fade : 1;
        ball(startX + (i % 10) * spacing, box.top + 170 + Math.floor(i / 10) * 72, "#137d89", "+", alphaP);
      }
      for (var j = 0; j < neg; j += 1) {
        var alphaN = j < paired ? 1 - fade : 1;
        ball(startX + (j % 10) * spacing, box.top + 360 + Math.floor(j / 10) * 72, "#c97918", "−", alphaN);
      }
      if (state.step >= 1) {
        for (var k = 0; k < paired; k += 1) {
          drawLine(vec(startX + (k % 10) * spacing, box.top + 200), vec(startX + (k % 10) * spacing, box.top + 330), "#705bc2", 3, [8, 7], 1 - fade);
        }
      }
      var result = pos - neg;
      drawText(pos + " + (−" + neg + ") = " + result, (box.left + box.right) / 2, box.bottom - 85, 34, result >= 0 ? "#137d89" : "#c97918");
    }

    function drawStep(progress) {
      if (LESSON_TYPE === "cylinder") drawCylinderLesson(progress);
      else if (LESSON_TYPE === "numberline") drawNumberlineLesson(progress);
      else if (LESSON_TYPE === "sunshadow") drawSunshadowLesson(progress);
      else if (LESSON_TYPE === "projection") drawProjectionLesson(progress);
      else if (LESSON_TYPE === "folding") drawFoldingLesson(progress);
      else if (LESSON_TYPE === "balls") drawBallsLesson(progress);
    }

    function updateDrag(event) {
      var rect = canvas.getBoundingClientRect();
      var x = (event.clientX - rect.left) * canvas.width / rect.width;
      var y = (event.clientY - rect.top) * canvas.height / rect.height;
      var box = sceneBox();
      if (LESSON_TYPE === "sunshadow") {
        var baseX = (box.left + box.right) / 2;
        var groundY = box.bottom - 95;
        state.sunSide = x < baseX ? -1 : 1;
        state.sunAngle = clamp(Math.atan2(groundY - y, Math.abs(x - baseX)) * 180 / Math.PI, 18, 78);
        renderControls(); renderLogic();
      } else if (LESSON_TYPE === "folding" && state.step >= 1) {
        state.foldPosition = clamp((x - box.left) / (box.right - box.left), 0.58, 0.9);
        renderControls(); renderLogic();
      } else if (LESSON_TYPE === "projection" && state.step >= 1) {
        state.objectX = clamp((x - (box.left + box.right) / 2) / 36, -3, 3);
        renderControls(); renderLogic();
      }
    }

    var draggingLessonObject = false;
    canvas.addEventListener("pointerdown", function (event) { draggingLessonObject = true; canvas.setPointerCapture(event.pointerId); updateDrag(event); });
    canvas.addEventListener("pointermove", function (event) { if (draggingLessonObject) updateDrag(event); });
    canvas.addEventListener("pointerup", function (event) { draggingLessonObject = false; canvas.releasePointerCapture(event.pointerId); });
'''


def _control_literal(spec: tuple[str, str, float, float, float, str]) -> str:
    key, label, minimum, maximum, step, unit = spec
    if key == "sunSide":
        formatter = 'function (v) { return Number(v) < 0 ? "左侧" : "右侧"; }'
    elif key == "projectionMode":
        formatter = 'function (v) { return Number(v) < 1 ? "中心投影" : "平行投影"; }'
    else:
        formatter = (
            "function (v) { return "
            + ("Number(v).toFixed(2)" if step < 0.1 else "Number(v).toFixed(1)" if step < 1 else "Math.round(v)")
            + f" + {json.dumps(unit, ensure_ascii=False)}; }}"
        )
    return (
        "{ key: "
        + json.dumps(key)
        + ", label: "
        + json.dumps(label, ensure_ascii=False)
        + f", min: {minimum}, max: {maximum}, step: {step}, format: {formatter} }}"
    )


def _steps_literal(requirement: dict, lesson_type: str) -> str:
    steps = requirement.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"需求缺少 steps：{requirement.get('title', '未知课题')}")
    controls = CONTROL_SPECS[lesson_type]
    logic_steps = LOGIC[lesson_type]
    result: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not str(step.get("title", "")).strip():
            raise ValueError(f"第 {index + 1} 个步骤缺少 title")
        step_controls = controls if index == len(steps) - 1 or len(steps) == 1 else []
        controls_text = ",\n          ".join(_control_literal(spec) for spec in step_controls)
        logic = logic_steps[min(index, len(logic_steps) - 1)]
        logic_text = ",\n          ".join(json.dumps(line, ensure_ascii=False) for line in logic)
        result.append(
            "      {\n"
            f"        title: {json.dumps(str(step['title']).strip(), ensure_ascii=False)},\n"
            f"        duration: {10000 if index else 8000},\n"
            f"        animated: {'true' if index > 0 or lesson_type in {'sunshadow', 'balls'} else 'false'},\n"
            f"        controls: [{controls_text}],\n"
            f"        logic: [{logic_text}]\n"
            "      }"
        )
    return "[\n" + ",\n".join(result) + "\n    ]"


def build_courseware(template: str, requirement: dict) -> str:
    title = str(requirement.get("title", "")).strip()
    lesson_type = LESSON_TYPES.get(title)
    if lesson_type is None:
        raise ValueError(f"没有经过确认的课件实现：{title}")

    output, count = re.subn(
        r'var TOOL_META = \{[\s\S]*?\n    \};',
        "var TOOL_META = {\n"
        f"      title: {json.dumps(title, ensure_ascii=False)}\n"
        "    };\n\n"
        f"    var LESSON_TYPE = {json.dumps(lesson_type)};",
        template,
        count=1,
    )
    if count != 1:
        raise ValueError("template 缺少唯一 TOOL_META 块")
    output, count = re.subn(
        r'var STEPS = \[[\s\S]*?\n    \];\n\n    var canvas',
        "var STEPS = " + _steps_literal(requirement, lesson_type) + ";\n\n    var canvas",
        output,
        count=1,
    )
    if count != 1:
        raise ValueError("template 缺少唯一 STEPS 块")
    state = '''var state = {
      step: 0,
      stepStart: performance.now(),
      manualProgress: null,
      panelWidth: 320,
      cylinderHeight: 5,
      circumference: 10,
      startValue: -2,
      moveDistance: 5,
      moveDirection: 1,
      sunAngle: 42,
      sunSide: 1,
      lightHeight: 5,
      objectX: 0,
      projectionMode: 0,
      foldPosition: 0.72,
      positiveCount: 7,
      negativeCount: 4
    };'''
    output, count = re.subn(
        r'var state = \{[\s\S]*?\n    \};', state, output, count=1
    )
    if count != 1:
        raise ValueError("template 缺少唯一 state 块")
    reset = '''function resetAll() {
      state.cylinderHeight = 5;
      state.circumference = 10;
      state.startValue = -2;
      state.moveDistance = 5;
      state.moveDirection = 1;
      state.sunAngle = 42;
      state.sunSide = 1;
      state.lightHeight = 5;
      state.objectX = 0;
      state.projectionMode = 0;
      state.foldPosition = 0.72;
      state.positiveCount = 7;
      state.negativeCount = 4;
      setStep(0);
    }'''
    output, count = re.subn(
        r'function resetAll\(\) \{[\s\S]*?\n    \}', reset, output, count=1
    )
    if count != 1:
        raise ValueError("template 缺少唯一 resetAll 函数")
    output, count = re.subn(
        r'function drawStep\(progress\) \{[\s\S]*?\n    \}\n\n    function draw\(\)',
        DRAWING_SCRIPT.strip() + "\n\n    function draw()",
        output,
        count=1,
    )
    if count != 1:
        raise ValueError("template 缺少唯一 drawStep 函数")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实三文件重建课件项目组")
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--target-parent", type=Path, required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--validate", type=Path, required=True)
    args = parser.parse_args()

    binding = ToolBinding(args.workflow, args.template, args.validate)
    project_service = ProjectService(ROOT / "resources")
    project_service.validate_tool_binding(binding)
    old_group = project_service.load_project_group(args.old_root)
    json_files: list[Path] = []
    requirements: list[dict] = []
    for project in old_group.projects:
        files = sorted((project.path / "原始需求").glob("*.json"))
        if len(files) != 1:
            raise ValueError(f"{project.path} 必须且只能包含一个原始 JSON")
        requirement = json.loads(files[0].read_text(encoding="utf-8-sig"))
        if str(requirement.get("title", "")).strip() not in LESSON_TYPES:
            raise ValueError(f"未确认的需求标题：{requirement.get('title')}")
        json_files.append(files[0])
        requirements.append(requirement)

    group = project_service.create_project_group(
        args.target_name,
        len(json_files),
        args.target_parent,
        json_files,
        binding,
    )
    template_text = (group.root / "公共工具" / "template.html").read_text(
        encoding="utf-8-sig"
    )
    task_service = TaskService(ROOT / "resources")
    acceptance = AcceptanceService(
        project_service, ArchiveService(), FeedbackService()
    )
    reports = []
    for project, requirement in zip(group.projects, requirements, strict=True):
        task_service.generate_first_build_task(
            project.path,
            "严格使用当前项目绑定的真实 workflow、template、validate；完成后执行完整产品验收。",
        )
        product = project.path / "产品迭代" / "初始版本.html"
        product.write_text(
            build_courseware(template_text, requirement), encoding="utf-8"
        )
        report = acceptance.run(group.root, project.path)
        reports.append(report)
        record = [
            "# 项目记录",
            "",
            "## 首次制作",
            "",
            f"课题：{requirement['title']}",
            f"产品版本：产品迭代/{product.name}",
            f"真实 workflow：{group.root / '公共工具' / 'WORKFLOW.md'}",
            f"真实 template：{group.root / '公共工具' / 'template.html'}",
            f"真实 validate：{group.root / '公共工具' / 'validate-tool.js'}",
            f"完整产品验收：{'通过' if report.passed else '未通过'}",
            f"项目记录：{report.markdown_path}",
            "",
        ]
        (project.path / "项目记录.md").write_text(
            "\n".join(record), encoding="utf-8"
        )

    failed = [report for report in reports if not report.passed]
    print(f"NEW_GROUP={group.root}")
    print(f"PROJECTS={len(group.projects)}")
    print(f"PASSED={len(reports) - len(failed)}")
    print(f"FAILED={len(failed)}")
    for report in reports:
        print(
            f"{Path(report.project_path).name}|"
            f"passed={report.passed}|warnings={report.warning_count}|"
            f"failures={report.failed_count}|report={report.markdown_path}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
