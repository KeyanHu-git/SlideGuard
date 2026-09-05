// Extend the existing reproduction workbook without changing its earlier sheets.
// Run with the bundled @oai/artifact-tool runtime. See docs/desktop-migration.md.
import fs from 'node:fs/promises';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';
const [input, outputDir, mode='inspect', dataPath] = process.argv.slice(2);
if(!input || !outputDir)throw new Error('Expected input.xlsx output-directory [inspect|edit] [log.json]');
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
await fs.mkdir(outputDir,{recursive:true});
console.log((await workbook.inspect({kind:'workbook,sheet,table',maxChars:5000,tableMaxRows:3,tableMaxCols:5})).ndjson);
if(mode==='inspect')process.exit(0);
if(mode==='preview'){
  const image=await workbook.render({sheetName:dataPath,range:'A1:F7',scale:1});
  await fs.writeFile(`${outputDir}/before.png`,new Uint8Array(await image.arrayBuffer()));
  process.exit(0);
}
if(mode!=='edit'||!dataPath)throw new Error('Unknown mode or missing log.json');
const records=JSON.parse(await fs.readFile(dataPath,'utf8'));
let sheet;
try{sheet=workbook.worksheets.getItem('Tauri迁移20260905')}catch{sheet=workbook.worksheets.add('Tauri迁移20260905')}
// Only this generated sheet is replaceable. Earlier manual sheets remain untouched.
if(sheet.tables.items.some(t=>t.name!=='DesktopMigration20260905'))throw new Error('Unexpected user table in generated sheet');
for(const existing of [...sheet.tables.items])existing.delete();
sheet.getUsedRange()?.clear({applyTo:'all'});
sheet.showGridLines=false;
const header=['步骤','操作与目的','复现入口','验收与结果','问题与处理','Linear'];
const rows=records.map(r=>[r.step,r.action,r.command,r.evidence,r.pitfall,r.issue]);
sheet.getRange(`A1:F${rows.length+1}`).values=[header,...rows];
const area=sheet.getRange(`A1:F${rows.length+1}`);
area.format.font={name:'Microsoft YaHei',size:11,color:'#253244'};
area.format.wrapText=true;area.format.verticalAlignment='top';
sheet.getRange('A1:F1').format={fill:'#24364B',font:{name:'Microsoft YaHei',size:11,bold:true,color:'#FFFFFF'},rowHeight:28};
for(const [col,width] of [['A',8],['B',36],['C',48],['D',46],['E',46],['F',35]])sheet.getRange(`${col}1:${col}${rows.length+1}`).format.columnWidth=width;
sheet.getRange(`A2:F${rows.length+1}`).format.rowHeight=52;
sheet.getRange(`A2:A${rows.length+1}`).setNumberFormat('0');
sheet.freezePanes.freezeRows(1);
const table=sheet.tables.add(`A1:F${rows.length+1}`,true,'DesktopMigration20260905');
table.style=workbook.worksheets.getItem('01_标准SOP').tables.items[0].style;
console.log((await workbook.inspect({kind:'table',range:`'Tauri迁移20260905'!A1:F4`,include:'values,formulas',tableMaxRows:4,tableMaxCols:6})).ndjson);
console.log((await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!',options:{useRegex:true,maxResults:20},summary:'formula error scan'})).ndjson);
for(const [name,range] of [['first','A1:F7'],['last',`A${Math.max(2,rows.length-4)}:F${rows.length+1}`]]){
  const image=await workbook.render({sheetName:'Tauri迁移20260905',range,scale:1});
  await fs.writeFile(`${outputDir}/${name}.png`,new Uint8Array(await image.arrayBuffer()));
}
const output=await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/PPTX_to_SVG_高保真导出_复现手册.xlsx`);
