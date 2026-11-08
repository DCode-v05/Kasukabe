import json
import csv
import io

def run(input_text: str, input_format: str = "json", output_format: str = "markdown") -> str:
    if input_format == "json":
        data = json.loads(input_text)
    elif input_format == "csv":
        reader = csv.DictReader(io.StringIO(input_text))
        data = list(reader)
    else:
        return json.dumps({"error": f"Unsupported input format: {input_format}"})

    if not isinstance(data, list):
        data = [data]

    if output_format == "json":
        return json.dumps(data, indent=2)
    elif output_format == "csv":
        if not data:
            return ""
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        return out.getvalue()
    elif output_format == "markdown":
        if not data:
            return "| (empty) |"
        headers = list(data[0].keys())
        lines = ["| " + " | ".join(headers) + " |"]
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in data:
            lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
        return "\n".join(lines)
    elif output_format == "yaml":
        lines = []
        for item in data:
            lines.append("---")
            for k, v in item.items():
                lines.append(f"{k}: {v}")
        return "\n".join(lines)
    else:
        return json.dumps({"error": f"Unsupported output format: {output_format}"})