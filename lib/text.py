def discovermaxlength(lengths, obj):
    for k, v in obj.items():
        if lengths.get(k) == None:
            lengths[k] = max(len(k), len(str(v)))
        else:
            lengths[k] = max(lengths[k], len(k), len(str(v)))

def dumpheader(lengths, obj):
    firstline, line, endline = "+", "|", "+"
    for k, v in obj.items():
        firstline += "-" + "-" * lengths[k] + "-+"
        if type(v) == int:
            line += " " +  k.center(lengths[k]) + " |"
        else:
            line += " " +  k.ljust(lengths[k]) + " |"
        endline += "=" + "=" * lengths[k] + "=+"
    return firstline + "\n" + line + "\n" + endline

def dumpdata(lengths, obj):
    interline, line = "+", "|"
    for k, v in obj.items():
        interline += "-" + "-" * lengths[k] + "-+"
        if type(v) == int:
            line += " " + str(v).rjust(lengths[k]) + " |"
        else:
            line += " " + str(v).ljust(lengths[k]) + " |"
    return "\n" + line + "\n" + interline

def dumps(data):
    buffer = ""
    lengths = {}
    for key, value in data.items():
        if isinstance(value, str):
            buffer += key + ": " + value

        if isinstance(value, dict):
            value = [ value ]

        if isinstance(value, list):
            for item in value:
                discovermaxlength(lengths, item)

            for idx in range(len(value)):
                if idx == 0:
                    buffer += dumpheader(lengths, value[0])
                buffer += dumpdata(lengths, value[idx])

    return buffer
