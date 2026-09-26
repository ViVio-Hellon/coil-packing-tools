Attribute VB_Name = "照合出力"
'==================================================================
' 照合出力 ── いまの画面に出ている値を、そのまま書き出す
'
' 【なにをするもの】
' 移植したPython版と現行VBAの答えを突き合わせるために、
' **UF_Material の画面に出ている値を丸ごと CSV へ書き足す**マクロです。
'
' いつもどおり
'   LotNo を入れる → 受注番号を選ぶ → 検入数を入れる → 計算Start
' と操作して、フォームを開いたまま Alt+F8 でこのマクロを走らせます。
'
' 【入れ方】
' 1. Alt+F11 でVBEを開く
' 2. 前の版の「照合出力」モジュールがあれば、右クリック → 解放(エクスポートしない)
' 3. 挿入 → 標準モジュール
' 4. このファイルの中身を貼り付ける(この行から下ぜんぶ)
' 5. 画面に戻って Alt+F8 →「照合_この画面を書き出す」
'
' 【書き出し先】
' ブックと同じフォルダの `照合_yyyymmdd.csv`。
' そこへ書けないとき(読み取り専用の共有など)はデスクトップ。
'
' 【1行の中身】 1行 = 画面の1欄
'   書き出し時刻, LotNo, 検入数, コントロール名, 種類, 値, 文字色, 枠色
' 例)
'   "2026-09-23 10:15:02","L6052G0","6","積数","TextBox","3","-2147483640",""
'   "2026-09-23 10:15:02","L6052G0","6","F枚数","Label","枚数","255",""
' 文字色 255 = 赤、枠色 16776960 = 水色。
' 値の中の改行は `\n` の2文字にして1行に収めています。
'
' 【珍しいロットを取りこぼさない(任意)】
' 珍しい仕様のロットは「来ないときは来ない」。押し忘れないように、
' 計算Start のたびに**黙って**書き出すこともできます。UF_Material の
' CommandButton5_Click のいちばん最後(End Sub の直前)に1行足すだけ:
'
'     Call 照合_黙って書き出す
'
' 知らせ(MsgBox)は一切出さず、書けなくても計算の邪魔をしません。
' やめるときはその1行を消すだけです。
'
' 【疑似ロット ── 台帳に来ない仕様を、ロットを待たずに確かめる】
' Alt+F8 →「照合_疑似ロットをまとめて計算」を1回押すだけ。
' 下の一覧のロットを1件ずつ画面に入れ、計算して、書き出します。
'
' 計算Start が読む画面の欄は 包装仕様NO・外径・製品単重・受注板厚・
' 受注板幅・検入数・梱包単位_重量・梱包単位_枚数 の8つだけ(あとは
' 梱包資材マスタ)。この8欄を フォーム展開 と同じ書式で画面に入れれば、
' 台帳に無いロットでも VBA そのもので計算できます。台帳には触りません。
'
' 計算Start ボタン(CommandButton5_Click)は Private でマクロから
' 押せないので、**ボタンの中身をそのまま写した手順**で計算します
' (進み具合の小窓だけ出しません)。写し間違いが無いことは、先頭の
' 対照(Q0118A0 = 実物で答え合わせ済みの L6052G0 と同じ値)で確かめます。
'
' 疑似ロットの LotNo は Q で始まる7文字(実物のロットと重ならない)。
'
' 【前の版の不具合】
' 前の版は `ctl.Object` で欄を読もうとして1欄目でエラーになり、
' 見出しの行だけ書いて止まっていました。この版は欄を直接読み、
' **1つの欄が読めなくても、その欄に理由を書いて次へ進みます**。
'==================================================================
Option Explicit

Private Const 区切り As String = ","

' 黙って書き出すときは True。知らせ(MsgBox)を出さない
Private 静か As Boolean

'------------------------------------------------------------------
' 計算Start のたびに呼ぶ入口(任意)。何があっても知らせを出さない
'------------------------------------------------------------------
Public Sub 照合_黙って書き出す()
    静か = True
    On Error Resume Next
    照合_この画面を書き出す
    静か = False
End Sub

'------------------------------------------------------------------
' 疑似ロットをまとめて計算して書き出す
'------------------------------------------------------------------
Public Sub 照合_疑似ロットをまとめて計算()
    Dim 一覧 As Collection, 行 As Variant, n As Long, 失敗 As String

    If Not UF_Material.Visible Then
        MsgBox "先に資材計算のフォーム(UF_Material)を開いてください。", vbExclamation
        Exit Sub
    End If
    Set 一覧 = 疑似ロット一覧()
    If MsgBox(一覧.Count & " 件の疑似ロットを計算して書き出します。" & vbLf & _
              "画面が何度か切り替わります。途中で知らせが出たら OK を押してください。", _
              vbOKCancel + vbQuestion) <> vbOK Then Exit Sub

    静か = True
    For Each 行 In 一覧
        疑似ロットを画面に入れる CStr(行)
        ' 計算が例外で止まっても、そこまでの画面は書き出して次へ進む
        ' (止まったこと自体が答え合わせの対象)
        On Error Resume Next
        計算Startと同じ
        If Err.Number <> 0 Then
            失敗 = 失敗 & Split(CStr(行), "|")(0) & ": " & Err.Description & vbLf
            Err.Clear
        End If
        On Error GoTo 0
        照合_この画面を書き出す
        n = n + 1
        DoEvents
    Next 行
    静か = False

    If 失敗 <> "" Then
        MsgBox n & " 件を書き出しました。計算の途中で止まったもの:" & vbLf & vbLf & _
               失敗 & vbLf & 保存先(), vbExclamation
    Else
        MsgBox n & " 件を書き出しました。" & vbLf & vbLf & 保存先(), vbInformation
    End If
End Sub

' 疑似ロットの一覧。1行 =
'   LotNo|包装仕様NO|製品単重|受注板厚|受注板幅|梱包単位_重量|梱包単位_枚数|
'   コイル外径_MAX|検入数|外径(空なら外径MAX)|納入先名称|取引先名称
' 重量・枚数・外径MAX の 0 は「データなし」(フォーム展開 と同じ)。
Private Function 疑似ロット一覧() As Collection
    Dim c As New Collection
    c.Add "Q0118A0|1C0118|222.16666|1.5|152|0|3|1000|6|||ｻﾝﾜｷﾝｿﾞｸ(ｶ ﾄｳｷﾖｳｴｲｷﾞﾖｳｼﾖ"   ' 対照。L6052G0 と同じ値 → 16/16 と同じ答えになるはず(受注 03041506 の値)
    c.Add "Q1188A0|1C1188|503.75|2.44|270|0|2|1200|3||ｶ)ｼﾏﾉ|ﾆﾂｹｲﾒﾀﾙ(ｶ ｵｵｻｶｼﾃﾝ"   ' EXPS・リプラサイズ可変(受注 55040968 の値)
    c.Add "Q1293A0|1C1293|106.475|1.4|47|1000|0|1160|10||ﾊﾟﾅｿﾆﾂｸ(ｶ)ﾊﾟﾅｿﾆﾂｸｴﾅｼﾞ-ﾅﾝﾀﾞﾝ|PEX GPRD POC 1"   ' EXPS・重量(受注 65018293 の値)
    c.Add "Q0125A0|1C0125|87.96538|1.2|42|500|0|1100|5||ｶ)ｿ-ﾃﾞﾅｶﾞﾉ|ﾆﾂｹｲｻﾝｷﾞﾖｳ(ｶ) ｹｲｱﾂｸﾞﾙ-ﾌﾟ"   ' ｸｰﾗｰﾌｨﾝ・重量(受注 20049615 の値)
    c.Add "Q1297A0|1C1297|135.4375|0.6|56|850|0|1100|6||ｶ)ｺﾄﾌﾞｷｾｲﾐﾂ|PEX GPRD POC 1"   ' ｺﾄﾌﾞｷｾｲﾐﾂ(受注 65020737 の値)
    c.Add "Q1258A0|1C1258|900|1|340|0|1|0|4|1180||ﾆﾂｹｲﾒﾀﾙ(ｶ)ｶｺｳﾋﾞｼﾞﾈｽﾕﾆﾂﾄ"   ' 全面・外径指定(段階表)(受注 12088522 の値)
    c.Add "Q1271A0|1C1271|164.85925|0.8|134|1000|0|900|6||ﾆｼﾊﾗﾘｺｳ(ｶ|ﾌｼﾞﾊﾂｼﾞﾖｳ(ｶ"   ' EXPS・1050指定・青・リプラ80(長さなし)(受注 65019697 の値)
    c.Add "Q1277A0|1C1277|151.65|0.3|89|0|3|930|7||ｶ)ﾊﾂﾀﾞｲｾｲｻｸｼﾖ|ｶ)ｴｽﾀﾞﾂﾄ"   ' EXPS・950指定・脚高・チップ(受注 54034293 の値)
    c.Add "Q1289A0|1C1289|262.51111|1|56|900|0|1500|5||ｶ)ｺﾄﾌﾞｷｾｲﾐﾂ|PEX GPRD POC 1"   ' EXPS・1500指定・脚狭・クラフト(受注 65017170 の値)
    c.Add "Q1262A0|1C1262|937.4|1.5|217|0|2|1500|5||ﾆﾂｹｲｷﾝｱｸﾄ(ｶ)ﾆｲｶﾞﾀｺｳｼﾞﾖｳ|ﾆﾂｹｲｷﾝｱｸﾄ(ｶ)ﾆｲｶﾞﾀｺｳｼﾞﾖｳ"   ' 全面・リプラ40・枚数範囲max(受注 23007826 の値)
    c.Add "Q1125A0|1C1125|107.31176|1.6|73|1000|0|950|10||ﾆﾂｹｲｷﾝｱﾙﾓ(ｶ|ﾆﾂｹｲｷﾝｱﾙﾓ(ｶ"   ' 全面・高さで決まる(L716N52 の値)(受注 18036491 の値)
    c.Add "Q1103A0|1C1103|530.5|1.5|499|0|2|900|6|760|ｲｽﾞﾐﾒﾀﾙ(ｶ|ｲｽﾞﾐﾒﾀﾙ(ｶ"   ' ｲｽﾞﾐﾒﾀﾙ特例・800未満全面(枚数2を足した)(受注 13067400 の値)
    c.Add "Q1252A0|1C1252|300|1|150|0|3|1100|7||ﾖｼｶﾜｺｳｷﾞﾖｳ|"   ' 全面・1本積み不可で振り分け(吉川工業)(作った値)
    c.Add "Q1260A0|1C1260|119.92|2|100|0|3|900|6||ｷﾖｳﾎｳｾｲｻｸｼﾖ|"   ' スカシ・ｺｲﾙ間は間紙入〇(協豊製作所)(作った値)
    c.Add "Q1250A0|1C1250|119.92|2|100|0|2|900|5|||"   ' スカシ・上フタ井桁状・外装紙(作った値)
    c.Add "Q1127A0|1C1127|250|0.5|300|1000|0|1000|5|||"   ' ｸｰﾗｰﾌｨﾝ・EX・外装紙(作った値)
    c.Add "Q1178A0|1C1178|400|1|200|0|2|1200|6|||"   ' スカシ・1250指定・強度アップ・リプラ40(作った値)
    c.Add "Q1253A0|1C1253|200|0.5|250|0|2|800|5|||"   ' ｸｰﾗｰﾌｨﾝ・820指定・リプラ30(作った値)
    c.Add "Q1201A0|1C1201|150|0.1|400|1000|0|900|5|||"   ' ﾎｲｰﾙ(作った値)
    Set 疑似ロット一覧 = c
End Function

' 1行を画面に入れる。フォーム展開 と同じ書式にする
Private Sub 疑似ロットを画面に入れる(ByVal 行 As String)
    Dim v As Variant
    v = Split(行, "|")
    With UF_Material
        .積数.Tag = ""            ' 積数を手で直した名残があると画面が消えない
        .LOT = v(0)               ' 7文字 → フォームクリア → LOT検索(見つからず何もしない)
        .受注番号.Clear
        .受注番号 = ""
        .包装仕様NO = Left(v(1), 6)
        .製品単重 = Format(Val(v(2)), "0.00")
        .受注板厚 = Format(Val(v(3)), "0.000")
        .受注板幅 = Format(Val(v(4)), "0.0")
        If Val(v(5)) <> 0 Then
            .梱包単位_重量 = Val(v(5))
        Else
            .梱包単位_重量 = "データなし"
        End If
        If Val(v(6)) <> 0 Then
            .梱包単位_枚数 = Val(v(6))
        Else
            .梱包単位_枚数 = "データなし"
        End If
        If Val(v(7)) <> 0 Then
            .コイル外径_MAX = Val(v(7))
            .外径 = Val(v(7))
        Else
            .コイル外径_MAX = "データなし"
        End If
        .コイル外径_目標 = "データなし"
        .コイル外径_MIN = "データなし"
        .コイル内径_目標 = "データなし"
        If v(9) <> "" Then .外径 = v(9)
        .検入数 = v(8)
        .納入先名称 = v(10)
        .取引先名称 = v(11)
    End With
End Sub

' 計算Start(UF_Material.CommandButton5_Click)の中身を、そのまま写したもの。
' ボタンは Private でマクロから押せないので、同じ手順をここで踏む。
' 進み具合の小窓(UFProgress)だけは出さない(計算には関わらない)。
Private Sub 計算Startと同じ()
    Dim PackagingNO As String, 外径 As Single, Pallet種類 As String
    If UF_Material.検入数 = 0 Then Exit Sub
    PackagingNO = UF_Material.包装仕様NO.Caption
    外径 = Val(UF_Material.外径)
    Call ソフトクリア
    UF_Material.パレット種類 = PalletType(PackagingNO, Val(UF_Material.外径))
    Pallet種類 = UF_Material.パレット種類.Caption
    UF_Material.パレット名称 = PalletSize(Pallet種類, 外径)
    UF_Material.台数 = 台数計算(PackagingNO, Val(UF_Material.製品単重), _
        Val(UF_Material.受注板厚), Val(UF_Material.受注板幅), Val(UF_Material.検入数))
    UF_Material.TotalC = リプラ計算(PackagingNO, Val(UF_Material.受注板厚), _
        Val(UF_Material.受注板幅))
End Sub

'------------------------------------------------------------------
' 本体
'------------------------------------------------------------------
Public Sub 照合_この画面を書き出す()
    Dim ctl As Object
    Dim path As String, 行 As String, 時刻 As String
    Dim f As Integer, 新規 As Boolean
    Dim lot As String, ins As String
    Dim 種類 As String, 値 As String
    Dim 件数 As Long, 読めず As Long

    ' --- 画面が開いているか -----------------------------------
    lot = 欄の文字(UF_Material.Controls, "LOT")
    ins = 欄の文字(UF_Material.Controls, "検入数")
    If lot = "" Then
        If 静か Then Exit Sub
        MsgBox "UF_Material に LotNo が入っていません。" & vbLf & _
               "フォームを開いたまま、いつもどおり計算してから押してください。", _
               vbExclamation
        Exit Sub
    End If

    ' --- 書き出し先を開く -------------------------------------
    path = 保存先()
    新規 = (Dir(path) = "")
    f = FreeFile
    On Error GoTo 開けない
    Open path For Append As #f
    On Error GoTo 0

    ' ここから先で思わぬことが起きても、ファイルを閉じて何件書けたかを言う
    On Error GoTo 途中で止まった
    If 新規 Then
        Print #f, "書き出し時刻,LotNo,検入数,コントロール,種類,値,文字色,枠色"
    End If
    時刻 = Format(Now, "yyyy-mm-dd hh:nn:ss")

    ' --- 画面の欄を端から読む ---------------------------------
    ' 1つ読めなくても止めない。読めなかった欄は値に理由を書く
    For Each ctl In UF_Material.Controls
        種類 = TypeName(ctl)
        If 書く種類(種類) Then
            値 = 値を読む(ctl, 種類)
            If Left$(値, 5) = "#読めず:" Then 読めず = 読めず + 1
            行 = 包む(時刻) & 区切り & 包む(lot) & 区切り & 包む(ins) & 区切り & _
                 包む(名前(ctl)) & 区切り & 包む(種類) & 区切り & 包む(値) & 区切り & _
                 包む(色(ctl, "ForeColor")) & 区切り & 包む(色(ctl, "BorderColor"))
            Print #f, 行
            件数 = 件数 + 1
        End If
    Next ctl

    Close #f
    On Error GoTo 0

    ' --- 結果を言う。0欄なら失敗として言う --------------------
    If 静か Then Exit Sub
    If 件数 = 0 Then
        MsgBox "1欄も書き出せませんでした。" & vbLf & _
               "この画面のスクリーンショットと一緒に知らせてください。" & vbLf & vbLf & _
               path, vbCritical
    ElseIf 読めず > 0 Then
        MsgBox lot & " を書き出しました(" & 件数 & " 欄、うち " & 読めず & _
               " 欄は読めず)。" & vbLf & vbLf & path, vbExclamation
    Else
        MsgBox lot & " を書き出しました(" & 件数 & " 欄)。" & vbLf & vbLf & path, _
               vbInformation
    End If
    Exit Sub

開けない:
    If 静か Then Exit Sub
    MsgBox "書き出し先を開けませんでした: " & Err.Description & vbLf & vbLf & path, _
           vbCritical
    Exit Sub

途中で止まった:
    If Not 静か Then
        MsgBox "書き出しの途中で止まりました(" & 件数 & " 欄まで書けています): " & _
               Err.Description & vbLf & vbLf & path, vbCritical
    End If
    On Error Resume Next
    Close #f
End Sub

'------------------------------------------------------------------
' どこへ書いたか分からなくなったとき
'------------------------------------------------------------------
Public Sub 照合_保存先を表示する()
    Dim path As String: path = 保存先()
    If Dir(path) = "" Then
        MsgBox "まだ書き出していません。書き出すとここに作られます:" & vbLf & vbLf & path
    Else
        MsgBox "ここに書き出しています:" & vbLf & vbLf & path & vbLf & vbLf & _
               "このファイルを渡してください。", vbInformation
    End If
End Sub

'==================================================================
' 中で使うもの ── どれも例外を外へ出さない
'==================================================================

' 書き出す種類。ボタン・画像・スピンボタンは値を持たないので飛ばす
Private Function 書く種類(ByVal 種類 As String) As Boolean
    Select Case 種類
        Case "TextBox", "ComboBox", "Label", "Frame", _
             "CheckBox", "OptionButton", "ToggleButton", "ListBox"
            書く種類 = True
        Case Else
            書く種類 = False
    End Select
End Function

' 欄の値。種類ごとに読むところが違う。読めなければ理由を返す
Private Function 値を読む(ByVal ctl As Object, ByVal 種類 As String) As String
    Dim v As Variant
    On Error GoTo だめ
    Select Case 種類
        Case "Label", "Frame", "CheckBox", "OptionButton", "ToggleButton"
            If 種類 = "Label" Or 種類 = "Frame" Then
                v = ctl.Caption
            Else
                v = ctl.Value
            End If
        Case Else                                   ' TextBox / ComboBox / ListBox
            v = ctl.Value
    End Select
    If IsNull(v) Then v = ""
    値を読む = 一行にする(CStr(v))
    Exit Function
だめ:
    値を読む = "#読めず:" & Err.Description
End Function

' 欄の名前
Private Function 名前(ByVal ctl As Object) As String
    On Error Resume Next
    名前 = ctl.Name
End Function

' 色。その種類が持っていなければ空
Private Function 色(ByVal ctl As Object, ByVal どれ As String) As String
    Dim v As Variant
    On Error GoTo だめ
    If どれ = "ForeColor" Then
        v = ctl.ForeColor
    Else
        v = ctl.BorderColor
    End If
    色 = CStr(CLng(v))
    Exit Function
だめ:
    色 = ""
End Function

' 名前で欄を探して文字を読む(フォームが無くても止まらない)
Private Function 欄の文字(ByVal ctls As Object, ByVal 欄名 As String) As String
    On Error GoTo だめ
    欄の文字 = CStr(ctls(欄名).Value)
    Exit Function
だめ:
    欄の文字 = ""
End Function

' 書き出し先。ブックの隣 → 無理ならデスクトップ
Private Function 保存先() As String
    Dim folder As String
    folder = ThisWorkbook.path
    If folder = "" Then folder = Environ$("USERPROFILE") & "\Desktop"
    If Not 書けるか(folder) Then folder = Environ$("USERPROFILE") & "\Desktop"
    保存先 = folder & "\照合_" & Format(Date, "yyyymmdd") & ".csv"
End Function

' そのフォルダへ書けるか。共有が読み取り専用のことがある
Private Function 書けるか(ByVal folder As String) As Boolean
    Dim ためし As String, f As Integer
    ためし = folder & "\~照合書き込み確認.tmp"
    On Error GoTo だめ
    f = FreeFile
    Open ためし For Output As #f
    Close #f
    Kill ためし
    書けるか = True
    Exit Function
だめ:
    書けるか = False
End Function

' 改行を `\n` にして1行へ
Private Function 一行にする(ByVal s As String) As String
    s = Replace(s, vbCrLf, "\n")
    s = Replace(s, vbCr, "\n")
    s = Replace(s, vbLf, "\n")
    一行にする = s
End Function

' CSV の1マス。中の " は "" にする
Private Function 包む(ByVal s As String) As String
    包む = """" & Replace(s, """", """""") & """"
End Function
