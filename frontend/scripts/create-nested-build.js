#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

/**
 * 创建嵌套的构建目录结构
 * 将 build/ 目录下的内容移动到 build/aiop-pams/ 下
 */

const buildDir = path.join(__dirname, '..', 'build');
const nestedDir = path.join(buildDir, 'aiop-pams');

console.log('🔄 创建嵌套构建目录结构...');

try {
  // 检查 build 目录是否存在
  if (!fs.existsSync(buildDir)) {
    console.error('❌ build 目录不存在，请先运行 npm run build');
    process.exit(1);
  }

  // 创建 aiop-pams 子目录
  if (!fs.existsSync(nestedDir)) {
    fs.mkdirSync(nestedDir, { recursive: true });
    console.log('✅ 创建 aiop-pams 子目录');
  }

  // 获取 build 目录下的所有文件和目录（除了 aiop-pams）
  const items = fs.readdirSync(buildDir);

  // 移动文件到嵌套目录（除了 index.html）
  items.forEach(item => {
    if (item !== 'aiop-pams' && item !== 'index.html') {
      const sourcePath = path.join(buildDir, item);
      const targetPath = path.join(nestedDir, item);

      if (fs.statSync(sourcePath).isDirectory()) {
        // 移动目录
        fs.renameSync(sourcePath, targetPath);
        console.log(`📁 移动目录: ${item} -> aiop-pams/${item}`);
      } else {
        // 移动文件
        fs.renameSync(sourcePath, targetPath);
        console.log(`📄 移动文件: ${item} -> aiop-pams/${item}`);
      }
    }
  });

  // 更新 index.html 中的路径引用，指向 aiop-pams 目录
  const indexPath = path.join(buildDir, 'index.html');
  if (fs.existsSync(indexPath)) {
    let indexContent = fs.readFileSync(indexPath, 'utf8');

    // 更新静态资源路径，添加 aiop-pams/ 前缀
    indexContent = indexContent.replace(/src="\/aiop-pams\//g, 'src="/aiop-pams/');
    indexContent = indexContent.replace(/href="\/aiop-pams\//g, 'href="/aiop-pams/');

    fs.writeFileSync(indexPath, indexContent);
    console.log('✅ 更新 index.html 中的路径引用');
  }

} catch (error) {
  console.error('❌ 创建嵌套目录结构失败:', error.message);
  process.exit(1);
}
