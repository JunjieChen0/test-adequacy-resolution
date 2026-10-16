# 参考文献核验报告

**日期**：2026-10-16　**执行**：ClawsGO Science Agent
**对象**：`paper/refs.bib` 全部 23 条
**方法**：Crossref DOI 直接解析（`api.crossref.org/works/<DOI>`）逐条比对字段；对无 DOI、
解析失败或存疑的条目改用 Crossref 题名反查 + arXiv/Semantic Scholar 交叉确认。

---

## 汇总

| 等级 | 数量 |
|---|---|
| ✅ 核验一致 | 14 |
| 🔴 必须修正 | 9 |
| ⚠️ 建议核对 | 0 |

**9 条错误中，3 条属于最严重的一类：DOI 可解析，但指向的是另一篇论文。**
如果按原稿投出去，审稿人点开任一条就能发现，且这类错误会直接质疑整篇论文的
数据处理可信度。

---

## 🔴 必须修正

### 一、DOI 张冠李戴（3 条）

| 条目 | 原 DOI 实际指向 | 已改为 |
|---|---|---|
| `just2014defects4j` | *CrashLocator: locating crashing faults based on crash stacks*（Wu, Zhang, Cheung, Kim；ISSTA 2014, pp. 204–214） | `10.1145/2610384.2628055`，pp. 437–440 |
| `widyasari2020bugsinpy` | *IntelliCode compose: code generation using transformer*（Svyatkovskiy et al.；ESEC/FSE 2020, pp. 1433–1443） | `10.1145/3368089.3417943`，pp. 1556–1560 |
| `briand2000recapture` | *Validating the ISO/IEC 15504 measure of software requirements analysis process capability*（El Emam, Birk；TSE 26(6):541–566） | `10.1109/32.852741`，TSE 26(6):518–540 |

两条基准论文的 DOI 全错，位置就在正文第 1 节引用数据集的地方。

### 二、作者名错误或张冠李戴（3 条）

| 条目 | 原值 | 出版方记录 |
|---|---|---|
| `ayad2025detecting` | Ayad, **Ahmed**；AlBlwi, **Amani** | Ayad, **Amani**；AlBlwi, **Samia** |
| `jain2023mindthegap` | Kalburgi, **Sarthak** | Kalburgi, **Goutamkumar Tulajappa** |
| `paul2026beyond` | Paul, **Rohan** | Paul, **Partha Protim** |

`ayad2025detecting` 那条是典型的错位：第二作者的姓被误当成第一作者的名，整串名字平移了一位。

### 三、作者漏人（1 条）

`widyasari2020bugsinpy` 原条目 13 位作者，出版方记录为 **16 位**：

- **漏**：Yieh, Yuheng；Goh, Brian；Kang, Hong Jin
- **错名**：`Bui, Thong` → `Hoang, Thong`
- **多余**：`Jiang, Lingxiao` 不在作者列表中

### 四、题名错误（2 条）

| 条目 | 原题名 | 正确题名 |
|---|---|---|
| `ayad2025detecting` | Detecting faults vs. **revealing** failures: **On the measurement of test effectiveness** | Detecting faults vs. **exposing** failures: **Orthogonal measures of test suite effectiveness** |
| `paul2026beyond` | Beyond coverage and kill scores: **Measuring behavioural verification gaps in test suites** | Beyond coverage and kill scores: **Empirically measuring test suite behavioural gaps** |

### 五、文献类型与出处错误（2 条）

**`jain2023mindthegap`** 原记为 `@article` + `arXiv preprint`（无编号），实际已正式发表：
ISSRE 2023, pp. 102–113，DOI `10.1109/ISSRE59848.2023.00036`。已改为 `@inproceedings`。

**`liang1986longitudinal`** 原记为 `@book`，书名为 *Longitudinal Data Analysis*，
出版社 Oxford University Press，1986。**Liang 与 Zeger 没有出过这本书**；
该书的实际作者是 Fitzmaurice 等（Chapman & Hall, 2008）。两位作者 1986 年的经典工作是：

> Liang, K.-Y., Zeger, S. L. (1986). Longitudinal data analysis using generalized
> linear models. *Biometrika*, 73(1), 13–22. doi:10.1093/biomet/73.1.13

正文两处引用（第 2 节与 RQ 相关方法的层级结构段）语义都是「整群相关观测的推断」，
改引 Biometrika 论文后完全贴合。已改为 `@article`。

**`paul2026beyond`** 补全 arXiv 编号 `arXiv:2606.10417`（2026-06-09 提交，cs.SE）。
核验过：确为预印本，无期刊/会议出处；引用时须保持"预印本"标注。

---

## ✅ 核验一致的条目（14 条）

以下条目逐字段与 Crossref 记录完全一致，仅补入了原先缺失的卷期页：

| 条目 | 卷期页 |
|---|---|
| `inozemtseva2014coverage` | ICSE 2014, 435–445 |
| `papadakis2018mutation` | ICSE 2018, 537–548 |
| `niedermayr2016pseudo` | CSED 2016, 23–29 |
| `vera2019comprehensive` | EMSE 24(3), 1195–1225 |
| `chekam2017empirical` | ICSE 2017, 597–608 |
| `simpson1951interpretation` | JRSS-B 13(2), 238–241 |
| `ioannidis2005contamination` | PLoS Medicine 2(8), e124 |
| `hoenig2001abuse` | The American Statistician 55(1), 19–24 |
| `roberts2017crossvalidation` | Ecography 40(8), 913–929（14 位作者全对） |
| `cameron2008bootstrap` | REStat 90(3), 414–427 |
| `kitchenham2007cross` | TSE 33(5), 316–329（作者补中间名缩写 A.） |
| `zimmermann2009cross` | ESEC/FSE 2009, 91–100 |
| `nagappan2006mining` | ICSE 2006, 452–461 |
| `wooldridge2010econometric` / `cohen1988statistical` | 专著，版次与出版社核对无误（补出版地） |

另：`schuler2011checked` 原先既无 DOI 也无页码，已补 ICST 2011, pp. 90–99,
DOI `10.1109/ICST.2011.32`。Schuler 与 Zeller 另有 2013 年 STVR 期刊版
（23(7):531–551）；正文引用的是会议版，保持不变。

---

## 遗留判断（非核验问题，供投稿前斟酌）

1. **Briand 条目的引用位置**：正文写"整群相关观测的推断工具在软件工程文献里是标准做法
   \cite{briand2000recapture}"，但该文是缺陷数估计的 capture-recapture 方法，与
   "混合效应模型/整群稳健标准误"并不贴合。这不是文献错误，是引用选择问题。
   若要更贴题，可换用软件工程中混合效应模型的实证文献。**未擅自更换，待定。**
2. **两条预印本**（`jain2023mindthegap` 已改为正式发表；`paul2026beyond` 仍是预印本）。
   后者与本文主题最接近，若无正式版，措辞上需保持"预印本工作"的限定。
3. **自引/相关度**：`ayad2025detecting` 与 `paul2026beyond` 是与本文最接近的两项前人工作，
   建议在相关工作部分明确写清与本文的分工，避免审稿人认为重叠。

---

*ClawsGO Science Agent · 2026-10-16*
