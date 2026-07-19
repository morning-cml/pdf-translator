"""生成一个模拟计算机领域论文的测试 PDF（双栏、含图形与公式、含术语）。

用于离线验证：分栏解析、术语库、图形/公式保留、译文回填。
运行：python samples/make_sample.py
输出：samples/sample_paper.pdf
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "sample_paper.pdf"

TITLE = "A Study of Convolutional Neural Networks for Image Classification"
AUTHORS = "Jane Doe, John Smith    Department of Computer Science"

LEFT = [
    ("Abstract", "head"),
    ("Deep learning has become the dominant approach for computer "
     "vision. In this paper we study convolutional neural networks and "
     "the attention mechanism for image classification. We show that "
     "batch normalization and dropout improve generalization and reduce "
     "overfitting on the test set.", None),
    ("1. Introduction", "head"),
    ("Machine learning models learn representations from data. A neural "
     "network is trained with stochastic gradient descent and "
     "backpropagation to minimize a loss function. Recent transformer "
     "architectures rely on self-attention to capture long-range "
     "dependencies between tokens in a sequence.", None),
]

RIGHT = [
    ("2. Method", "head"),
    ("Our encoder uses convolution and max pooling to extract a feature "
     "map. The learning rate and weight decay are tuned on a validation "
     "set. We apply data augmentation to the training set to improve "
     "generalization of the deep neural network.", None),
    ("The objective function is defined as:", None),
    ("L = - sum_i y_i * log( softmax( z_i ) ) + lambda * || w ||^2", "eq"),
    ("3. Results", "head"),
    ("Our model achieves state of the art accuracy. An ablation study "
     "confirms that the attention mechanism is important for the final "
     "precision and recall on the benchmark.", None),
]


def wrap(text, font, size, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if stringWidth(trial, font, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_column(c, x, top_y, col_w, items, size=9, leading=12.5):
    y = top_y
    for text, kind in items:
        if kind == "head":
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(x, y, text)
            y -= leading
            continue
        if kind == "eq":
            c.setFont("Helvetica", 9)
            c.drawString(x + 6, y, text)
            y -= leading * 1.4
            continue
        c.setFont("Helvetica", size)
        for ln in wrap(text, "Helvetica", size, col_w):
            c.drawString(x, y, ln)
            y -= leading
        y -= 4
    return y


def main():
    w, h = A4
    c = canvas.Canvas(str(OUT), pagesize=A4)

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, h - 26 * mm, TITLE)
    c.setFont("Helvetica", 9)
    c.drawCentredString(w / 2, h - 32 * mm, AUTHORS)

    col_top = h - 44 * mm
    draw_column(c, 20 * mm, col_top, 72 * mm, LEFT)
    draw_column(c, 108 * mm, col_top, 82 * mm, RIGHT)

    fig_y = 55 * mm
    c.setStrokeColorRGB(0.2, 0.3, 0.8)
    c.setFillColorRGB(0.85, 0.9, 1.0)
    c.rect(20 * mm, fig_y, 72 * mm, 28 * mm, fill=1, stroke=1)
    c.setFillColorRGB(0.2, 0.3, 0.8)
    c.circle(40 * mm, fig_y + 14 * mm, 8 * mm, fill=1)
    c.setFillColorRGB(0.9, 0.4, 0.3)
    c.rect(56 * mm, fig_y + 7 * mm, 24 * mm, 14 * mm, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, fig_y - 5 * mm,
                 "Figure 1: Architecture of the convolutional neural network.")

    c.showPage()
    c.save()
    print("已生成:", OUT)


if __name__ == "__main__":
    main()
