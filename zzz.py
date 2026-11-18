import pandas as pd

df = pd.read_csv("kinopoisk-top250.csv")

b = df["country"].astype(str).str.split(",").explode().str.strip().value_counts()

print(int(b[b < 15].sum()))