---
runme:
  id: 01JESC2DCZD4H1MBCWQ8YFGAEP
  version: v3
---

```sh {"id":"01JESDWV2607M960BGS2F8BZZ2","name":"login-adc","terminalRows":"15"}
gcloud auth application-default login
```

```sh {"id":"01JESC2GX08PB8YDN0CPD14JFV","name":"rm-keys","promptEnv":"never","terminalRows":"3"}
rm -f /home/vscode/.foyle/openai_key_file
rm -f /home/vscode/.foyle/antropic_key_file
```

```sh {"id":"01JESC7JHJ5TZVE8MFHEDS2XKC","name":"set-keys"}
echo $OPENAI_API_KEY > /home/vscode/.foyle/openai_key_file
echo $ANTHROPIC_API_KEY > /home/vscode/.foyle/antropic_key_file
```

```sh {"background":"true","id":"01JESES9M1P39ADCJ6JJ13QXA9","interactive":"true","name":"foyle"}
foyle serve
```