'==============================================================================
' query_accdb.vbs
'   Reads a table from Access (.accdb) via ADODB (ACE OLEDB) and writes JSON
'   to standard output.
'
' Usage:
'   cscript //nologo //B query_accdb.vbs "<accdbPath>" "<tableName>" "<sqlFilter>"
'
' Output (pure ASCII; non-ASCII is escaped as \uXXXX):
'   {"ok":true,"fields":["..."],"records":[["...", ...], ...]}
'   {"ok":false,"error":"..."}
'
' THIS FILE MUST STAY PURE ASCII WITH CRLF LINE ENDINGS.
'   cscript.exe reads a .vbs in the machine's ANSI code page (CP932 here),
'   not UTF-8. Japanese comments saved as UTF-8 are misread, the script fails
'   to parse, and //B swallows the error -- you only get exit code 1 with an
'   empty stdout and stderr. Keeping this file ASCII makes it immune.
'
' Japanese notes live in app/repositories/access_bridge.py (the caller).
'==============================================================================
Option Explicit

Const adOpenStatic   = 3
Const adLockReadOnly = 1

Dim args, dbPath, tableName, sqlFilter
Set args = WScript.Arguments
If args.Count < 2 Then
    WriteOut "{""ok"":false,""error"":""missing arguments: dbPath, tableName""}"
    WScript.Quit 2
End If

dbPath    = args(0)
tableName = args(1)
If args.Count >= 3 Then sqlFilter = args(2) Else sqlFilter = ""

On Error Resume Next

Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(dbPath) Then
    WriteOut "{""ok"":false,""error"":""file not found: " & JsonEscape(dbPath) & """}"
    WScript.Quit 3
End If

' Try ACE 12.0 first (this is what the original VBA used), then 16.0.
' A machine with only a newer Access/Runtime installed has 16.0 registered
' but not 12.0; the data and the results are identical either way.
Dim cn, firstErr
Set cn = CreateObject("ADODB.Connection")
cn.Open "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" & dbPath & ";"
If Err.Number <> 0 Then
    firstErr = Err.Description
    Err.Clear
    cn.Open "Provider=Microsoft.ACE.OLEDB.16.0;Data Source=" & dbPath & ";"
End If
If Err.Number <> 0 Then
    WriteOut "{""ok"":false,""error"":""connect failed (ACE 12.0: " _
        & JsonEscape(firstErr) & " / ACE 16.0: " & JsonEscape(Err.Description) _
        & "). Check that the Access Database Engine is installed and that its " _
        & "bitness matches the cscript.exe in use (32-bit ACE needs " _
        & "SysWOW64\\cscript.exe).""}"
    WScript.Quit 4
End If

Dim sql
sql = "SELECT * FROM [" & tableName & "]"
If Len(sqlFilter) > 0 Then sql = sql & " WHERE " & sqlFilter

Dim rs
Set rs = CreateObject("ADODB.Recordset")
rs.Open sql, cn, adOpenStatic, adLockReadOnly
If Err.Number <> 0 Then
    WriteOut "{""ok"":false,""error"":""query failed: " & JsonEscape(Err.Description) & """}"
    cn.Close
    WScript.Quit 5
End If

On Error GoTo 0

Dim i, buf, fieldsBuf
fieldsBuf = ""
For i = 0 To rs.Fields.Count - 1
    If i > 0 Then fieldsBuf = fieldsBuf & ","
    fieldsBuf = fieldsBuf & """" & JsonEscape(rs.Fields(i).Name) & """"
Next

buf = "{""ok"":true,""fields"":[" & fieldsBuf & "],""records"":["

Dim first, rowBuf, v
first = True
Do Until rs.EOF
    rowBuf = ""
    For i = 0 To rs.Fields.Count - 1
        If i > 0 Then rowBuf = rowBuf & ","
        v = rs.Fields(i).Value
        If IsNull(v) Then
            rowBuf = rowBuf & "null"
        Else
            rowBuf = rowBuf & """" & JsonEscape(CStr(v)) & """"
        End If
    Next
    If Not first Then buf = buf & ","
    buf = buf & "[" & rowBuf & "]"
    first = False
    rs.MoveNext
Loop

buf = buf & "]}"

rs.Close
cn.Close
Set rs = Nothing
Set cn = Nothing

WriteOut buf
WScript.Quit 0


'--- JSON string escape ---
Function JsonEscape(s)
    Dim t, k, ch, code
    t = ""
    For k = 1 To Len(s)
        ch = Mid(s, k, 1)
        code = AscW(ch)
        Select Case ch
            Case """" : t = t & "\"""
            Case "\"  : t = t & "\\"
            Case vbCr : t = t & "\r"
            Case vbLf : t = t & "\n"
            Case vbTab: t = t & "\t"
            Case Else
                If code < 32 Then
                    t = t & "\u" & Right("000" & Hex(code), 4)
                ElseIf code < 0 Then
                    ' AscW returns negative for code points >= 32768
                    t = t & "\u" & Right("000" & Hex(code + 65536), 4)
                ElseIf code > 126 Then
                    t = t & "\u" & Right("000" & Hex(code), 4)
                Else
                    t = t & ch
                End If
        End Select
    Next
    JsonEscape = t
End Function

'--- standard output (already escaped to ASCII) ---
Sub WriteOut(s)
    WScript.StdOut.Write s
End Sub
