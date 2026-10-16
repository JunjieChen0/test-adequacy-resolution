# Reproducing the collection from scratch

The analysis can be reproduced from the shipped measurements without any of
this (see `README.md`). The steps below rebuild the measurements themselves.

## 1. Defects4J (Java)

Requirements: OpenJDK 11, Maven, Perl with `String::Interpolate`.

```bash
git clone https://github.com/rjust/defects4j.git
cd defects4j && ./init.sh
export PATH=$PWD/framework/bin:$PATH

# environment used by the collection scripts (JDK, Maven, Major, Perl modules)
source scripts/d4j-env.sh
```

Then, per project and set of faults:

```bash
python3 scripts/collect_subsets.py --project Math --bugs 1 2 3 \
        --fracs 0.1 0.25 --reps 1 --workers 3 --mut-classes 15
```

The work directory defaults to `~/d4j-work` and can be overridden with the
`D4J_WORK` environment variable.

Four implementation points matter for reproducibility:

1. **Subset construction.** The framework's `coverage` and `mutation` commands
   take no test list, and `all_tests` is rewritten by the framework. We exploit
   the fact that each project's build file defines test sources with a fileset
   over the *test source directory*, so relocating the sources of non-selected
   tests makes the framework build and run only the subset.
2. **Dependency closure.** Test classes reference each other, so the selected
   set is closed under the classes it names, otherwise the remaining tests do
   not compile.
3. **Adaptive restoration.** Aggregating test classes (`*PackageTests`) are
   compiled even when not run. When the build fails we parse `javac`'s
   missing-symbol errors and copy the named classes back from a staging area,
   iterating until compilation succeeds.
4. **Mutation on the fixed version.** Mutation scores are measured on the
   *fixed* version of each fault, because the buggy version's failing trigger
   tests abort the mutation analysis (`haltonfailure`).

## 2. BugsInPy (Python)

Requirements: Python 3.8, with one virtual environment per project.

```bash
git clone https://github.com/soarsmu/BugsInPy.git
python3 scripts/bip_meta.py                    # parse per-fault trigger tests
python3 scripts/bip_collect.py --project youtube-dl --fracs 0.1 0.3 0.6 \
        --only test/test_utils.py test/test_jsinterp.py test/test_YoutubeDL.py
```

Notes:

- The collector writes its scratch space under `data-bip/`: `repo/<project>`
  (one clone per project), `work/<project>` (the working tree it switches
  between faults) and `venv/<project>` (one virtual environment per project,
  since their dependency sets conflict). Together these reach about 900\,MB and
  are excluded from the package by `.gitignore`; delete them freely.
- `bugsinpy-checkout` performs a full `git clone` per fault (about ten minutes
  per fault in our measurements). This package instead clones each repository
  once and switches a shared working tree with `git reset --hard
  <fixed_commit>`, which brought the cost down to about ninety seconds per
  fault.
- The `requirements.txt` files shipped with BugsInPy are UTF-16LE, and the
  framework's own `bugsinpy-compile` fails on them with
  `Invalid requirement: ''`. Convert to UTF-8/LF and drop the self-referential
  `-e git+...#egg=<project>` line.
- Trigger tests are sometimes declared on a `unittest` base class that
  `pytest` does not collect directly (only its subclasses). We therefore match
  triggers on (file, method name) rather than on the full node identifier, and
  restrict each project's fault population to faults whose trigger falls inside
  the runnable pool, so that faults carried by network-dependent tests do not
  depress the outcome.
- Some projects need their test files passed explicitly rather than as a
  directory, because their test modules do not match pytest's default
  `python_files` patterns (tqdm names its tests `tests_*.py`).

## 3. Analysis

```bash
python3 scripts/analyze_all.py --benchmark both --boot 2000
python3 scripts/simulate_confounding.py
python3 scripts/plot_figures.py --lang en
```

## Known environment pitfalls

- A JDK whose `conf/` and `lib/security/` entries are symlinks into
  `/etc/java-<version>-openjdk` fails with
  `java.lang.InternalError: Error loading java.security file` when that
  directory is missing, *while `java -version` still works*. Restore the
  directory before investigating the collection scripts.
- `-A` is rejected by the Defects4J coverage wrapper; pass a class list with
  `-i` instead.
- `defects4j coverage` defaults to instrumenting only the modified class, which
  silently changes the meaning of the metric; we measured a factor of thirty
  between the default scope and the full class list.
- Running many JVMs concurrently exhausts memory; the driver runs one project
  at a time with a bounded worker count.
