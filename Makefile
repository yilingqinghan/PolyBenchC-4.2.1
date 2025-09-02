# === PolyBench: 各自生成 IR（kernel独立） -> opt -> 链接 polybench.o 成可执行 ===
SHELL := /bin/bash

# —— 你的 LLVM 工具链 —— #
TOOLROOT ?= /home/yz/clean/llvm-project/build/bin
CLANG    := $(TOOLROOT)/clang
OPT      := $(TOOLROOT)/opt

# 编译/链接选项
CFLAGS_BASE ?= -I utilities           # kernel 源码/IR 生成时用
IRFLAGS     ?= -Xclang -disable-llvm-passes -emit-llvm -S
POLYFLAGS   ?= -O3 -I utilities           # 编译 polybench.c 为 .o 时用
LDFLAGS     ?=
LDLIBS      ?= -lm

# 目录
IRDIR  := ir
OBJDIR := obj
BINDIR := bin_opt

# 自动枚举所有 benchmark 名（排除 utilities 与备份）
BENCHMARKS := $(shell find . -type f -name "*.c" \
                  -not -path "./utilities/*" \
                  -not -name "polybench.c" \
                  -not -name "Nussinov.orig.c" \
                  -printf "%f\n" | sed 's/\.c$$//' | sort -u)

IR_ALL      := $(addprefix $(IRDIR)/,$(addsuffix .ll,$(BENCHMARKS)))
IR_ALL_OPT  := $(addprefix $(IRDIR)/,$(addsuffix _opt.ll,$(BENCHMARKS)))
EXE_ALL     := $(addprefix $(BINDIR)/,$(BENCHMARKS))
POLY_OBJ    := $(OBJDIR)/polybench.o

.PHONY: all ll opt exe one show clean

# 一把梭到可执行
all: exe

# 只生成 kernel 的未优化 IR：ir/<name>.ll
ll: $(IRDIR) $(IR_ALL)

# 生成优化 IR：ir/<name>_opt.ll
opt: $(IRDIR) $(IR_ALL_OPT)

# 从 *_opt.ll + polybench.o 链接成可执行：bin_opt/<name>
exe: $(BINDIR) $(POLY_OBJ) $(EXE_ALL)

# ====== 规则区 ======

# 生成单个 kernel 的 .ll（不包含 polybench.c）
$(IRDIR)/%.ll: | $(IRDIR)
	@src=$$(find . -type f -name "$*.c" -not -path "./utilities/*"); \
	if [[ -z "$$src" ]]; then echo "找不到源码：$*.c"; exit 1; fi; \
	echo "$(CLANG) $(CFLAGS_BASE) $(IRFLAGS) $$src -o $@"; \
	$(CLANG) $(CFLAGS_BASE) $(IRFLAGS) $$src -o $@

# 对 .ll 跑 opt 得到 _opt.ll（默认 -O3，可改 OPTPASS）
OPTPASS ?= -O3
$(IRDIR)/%_opt.ll: $(IRDIR)/%.ll | $(IRDIR)
	@echo "$(OPT) $(OPTPASS) -S $< -o $@"
	$(OPT) $(OPTPASS) -S $< -o $@

# 编 polybench.c 为对象文件（单独模块）
$(POLY_OBJ): utilities/polybench.c | $(OBJDIR)
	@echo "$(CLANG) $(POLYFLAGS) -c $< -o $@"
	$(CLANG) $(POLYFLAGS) -c $< -o $@

# 链接：kernel_opt.ll + polybench.o -> 可执行
$(BINDIR)/%: $(IRDIR)/%_opt.ll $(POLY_OBJ) | $(BINDIR)
	@echo "$(CLANG) $< $(POLY_OBJ) -O3 -o $@ $(LDFLAGS) $(LDLIBS)"
	$(CLANG) $< $(POLY_OBJ) -O3 -o $@ $(LDFLAGS) $(LDLIBS)

# 便捷目标
one:
	@[[ -n "$(name)" ]] || { echo '用法: make one name=2mm'; exit 2; }
	$(MAKE) $(BINDIR)/$(name)

show:
	@echo "BENCHMARKS:"; echo "$(BENCHMARKS)" | tr ' ' '\n'

# 目录
$(IRDIR) $(OBJDIR) $(BINDIR):
	mkdir -p $@

clean:
	rm -rf $(IRDIR) $(OBJDIR) $(BINDIR)

