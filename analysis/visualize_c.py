import re
import pandas as pd
import matplotlib.pyplot as plt

INPUT_FILE = "lizard.txt"  # <-- save your output here

functions = []

# -------------------------
# Parse Lizard output
# -------------------------
with open(INPUT_FILE, "r") as f:
    for line in f:

        # match only function rows
        # format: NLOC CCN token PARAM length location
        match = re.match(r"\s*(\d+)\s+(\d+)\s+\d+\s+\d+\s+(\d+)\s+(.+)", line)

        if match:
            nloc = int(match.group(1))
            ccn = int(match.group(2))
            length = int(match.group(3))
            location = match.group(4).strip()

            # skip headers / garbage
            if "@" not in location:
                continue

            functions.append({
                "name": location,
                "nloc": nloc,
                "cyclomatic": ccn,
                "length": length
            })

df = pd.DataFrame(functions)

print("\nExtracted functions:")
print(df)

# -------------------------
# 1. Cyclomatic Complexity
# -------------------------
plt.figure()
df_sorted = df.sort_values("cyclomatic")
plt.barh(df_sorted["name"], df_sorted["cyclomatic"])
plt.title("Cyclomatic Complexity (Lizard)")
plt.xlabel("CCN")

plt.savefig("lizard_cyclomatic.png", dpi=300, bbox_inches="tight")
plt.close()

# -------------------------
# 2. Lines of Code (NLOC)
# -------------------------
plt.figure()
df_sorted = df.sort_values("nloc")
plt.barh(df_sorted["name"], df_sorted["nloc"])
plt.title("Lines of Code per Function (Lizard)")
plt.xlabel("NLOC")

plt.savefig("lizard_nloc.png", dpi=300, bbox_inches="tight")
plt.close()

# -------------------------
# 3. Function Length
# -------------------------
plt.figure()
df_sorted = df.sort_values("length")
plt.barh(df_sorted["name"], df_sorted["length"])
plt.title("Function Length (Lizard)")
plt.xlabel("Length")

plt.savefig("lizard_length.png", dpi=300, bbox_inches="tight")
plt.close()

print("\nSaved:")
print(" - lizard_cyclomatic.png")
print(" - lizard_nloc.png")
print(" - lizard_length.png")