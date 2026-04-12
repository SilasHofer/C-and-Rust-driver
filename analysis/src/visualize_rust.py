import json
import pandas as pd
import matplotlib.pyplot as plt

INPUT_FILE = "lib.rs.json"

# -------------------------
# Load JSON
# -------------------------
with open(INPUT_FILE, "r") as f:
    data = json.load(f)

functions = []

# -------------------------
# Recursive extraction
# -------------------------
def walk(node):
    if isinstance(node, dict):

        # function node
        if node.get("kind") == "function":
            metrics = node.get("metrics", {})

            functions.append({
                "name": f"{node.get('name')}:{node.get('start_line')}",
                "sloc": metrics.get("loc", {}).get("sloc", 0),
                "cyclomatic": metrics.get("cyclomatic", {}).get("sum", 0),
                "cognitive": metrics.get("cognitive", {}).get("sum", 0),
            })

        for v in node.values():
            walk(v)

    elif isinstance(node, list):
        for item in node:
            walk(item)

walk(data)

df = pd.DataFrame(functions)

print("\nExtracted functions:")
print(df)

# -------------------------
# 1. Cyclomatic Complexity
# -------------------------
df_sorted = df.sort_values("cyclomatic")

plt.figure()
plt.barh(df_sorted["name"], df_sorted["cyclomatic"])
plt.title("Cyclomatic Complexity per Function")
plt.xlabel("Cyclomatic Complexity")

plt.savefig("cyclomatic.png", dpi=300, bbox_inches="tight")
plt.close()

# -------------------------
# 2. Lines of Code
# -------------------------
df_sorted = df.sort_values("sloc")

plt.figure()
plt.barh(df_sorted["name"], df_sorted["sloc"])
plt.title("Lines of Code per Function")
plt.xlabel("SLOC")

plt.savefig("sloc.png", dpi=300, bbox_inches="tight")
plt.close()

# -------------------------
# 3. Cognitive Complexity
# -------------------------
df_sorted = df.sort_values("cognitive")

plt.figure()
plt.barh(df_sorted["name"], df_sorted["cognitive"])
plt.title("Cognitive Complexity per Function")
plt.xlabel("Cognitive Complexity")

plt.savefig("cognitive.png", dpi=300, bbox_inches="tight")
plt.close()

print("\nSaved plots:")
print(" - cyclomatic.png")
print(" - sloc.png")
print(" - cognitive.png")